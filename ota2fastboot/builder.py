"""Build pipeline: OTA directory in, fastboot package out."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from .constants import BLOCK, DEFAULT_DEVICE_SIZE, SUPER_GROUP
from .errors import ToolError
from .firmware import copy_firmware, firmware_size
from .log import Log
from .ota import TransferList, parse_op_list, read_props
from .package import check_script_contract, copy_scripts, write_package_env
from .report import render_build_md, write_manifest
from .sparse import probe_sparse_device_size
from .stream import extract_partition
from .super_image import build_super, lp_metadata
from .util import fmt_size, free_space_check, reuse_image
from .variants import choose_oem, choose_vgc, model_candidates, oem_variants
from .verify import fsck_images, verify_avb

REPO_ROOT = Path(__file__).resolve().parent.parent


@dataclass
class SuperPlan:
    sizes: dict[str, int]
    groups: dict[str, str]
    oem_name: str
    oem_note: str
    vgc_name: str
    vgc_note: str
    vm_tl: Path | None
    vm_transfer: TransferList | None
    vm_size: int
    device_size: int


def build(args: argparse.Namespace) -> int:
    package = args.package.resolve()
    out, display_out = _output_dir(args, package)
    scripts_dir = (args.scripts_dir or REPO_ROOT / "scripts").resolve()

    if not (package / "META-INF" / "com" / "android" / "metadata").is_file():
        print(f"error: {package} is not an extracted OTA package", file=sys.stderr)
        return 2

    code = _prepare_output(args, out)
    if code is not None:
        return code

    log = Log(out / "build.log")
    log.add_redaction(out, "<out>")
    log.add_redaction(package, "<ota>")
    log.add_redaction(REPO_ROOT, "<repo>")
    log.add_redaction(scripts_dir, "<scripts>")

    try:
        return _run(args, package, out, display_out, scripts_dir, log)
    except ToolError as error:
        log(f"ABORT: {error}")
        print(f"error: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        log("ABORT: interrupted")
        return 130
    finally:
        log.close()


def _output_dir(args: argparse.Namespace, package: Path) -> tuple[Path, str]:
    out = (args.output or REPO_ROOT / "out" / package.name.replace("-update-full", "")).resolve()
    try:
        display_out = out.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        display_out = out.name
    return out, display_out


def _prepare_output(args: argparse.Namespace, out: Path) -> int | None:
    if out.exists() and not out.is_dir():
        print(f"error: output path is not a directory: {out}", file=sys.stderr)
        return 2

    if out.exists() and any(out.iterdir()):
        if args.force:
            if out == REPO_ROOT or out.parent == out:
                print(f"error: refusing to delete {out}", file=sys.stderr)
                return 2
            shutil.rmtree(out)
        elif not args.resume:
            print(
                f"error: output directory {out} is not empty "
                "(use --resume, --force or --output)",
                file=sys.stderr,
            )
            return 2
    out.mkdir(parents=True, exist_ok=True)
    return None


def _load_props(package: Path, log: Log) -> dict[str, str]:
    props = read_props(package / "META-INF" / "com" / "android" / "metadata")
    if props.get("ota-type") != "BLOCK":
        raise ToolError(f"unsupported ota-type: {props.get('ota-type')}")
    if props.get("package-type") != "dota":
        log(f"warning: package-type is {props.get('package-type')}, expected dota")
    for required in ("pre-device", "post-build"):
        if required not in props:
            raise ToolError(f"OTA metadata lacks {required}")
    if props.get("anti-rollback-level") not in (None, "0"):
        log(f"warning: anti-rollback-level={props['anti-rollback-level']}")
    return props


def plan_super(
    package: Path,
    props: dict[str, str],
    oem_arg: str,
    vgc_arg: str,
    device_size: int | None,
    log: Log,
) -> SuperPlan:
    op_list = package / "dynamic_partitions_op_list"
    if not op_list.is_file():
        raise ToolError("dynamic_partitions_op_list not found")
    sizes, groups = parse_op_list(op_list)
    sizes = {name: size for name, size in sizes.items() if name != "scratch"}
    if not sizes:
        raise ToolError("op list defines no partitions")
    for name in sizes:
        if name in {"oem", "vgc"}:
            raise ToolError(f"unexpected {name} in the main op list")

    oem_name, oem_note = choose_oem(oem_arg, package, props)
    vgc_name, vgc_note = choose_vgc(vgc_arg, package)

    if oem_name != "none":
        oem_sizes, oem_groups = parse_op_list(
            package / "oem" / f"dynamic_partitions_op_list_{oem_name}"
        )
        sizes.update(oem_sizes)
        groups.update(oem_groups)
        for name in oem_sizes:
            groups.setdefault(name, SUPER_GROUP)
    if vgc_name != "none":
        vgc_sizes, vgc_groups = parse_op_list(
            package / "vgc" / f"dynamic_partitions_op_list_{vgc_name}"
        )
        sizes.update(vgc_sizes)
        groups.update(vgc_groups)
        for name in vgc_sizes:
            groups.setdefault(name, SUPER_GROUP)

    vm_tl = package / "vm-bootsys.transfer.list"
    vm_transfer = TransferList(vm_tl) if vm_tl.is_file() else None
    vm_size = vm_transfer.max_block * BLOCK if vm_transfer else 0

    if device_size is None:
        device_size = DEFAULT_DEVICE_SIZE
        log(
            f"super size: default {fmt_size(device_size)} "
            "(override with --device-size for other devices)"
        )

    return SuperPlan(
        sizes=sizes,
        groups=groups,
        oem_name=oem_name,
        oem_note=oem_note,
        vgc_name=vgc_name,
        vgc_note=vgc_note,
        vm_tl=vm_tl if vm_transfer else None,
        vm_transfer=vm_transfer,
        vm_size=vm_size,
        device_size=device_size,
    )


def extract_partitions(
    package: Path,
    out_fw: Path,
    raw_dir: Path,
    plan: SuperPlan,
    args: argparse.Namespace,
    log: Log,
) -> dict[str, Path]:
    if plan.vm_transfer:
        log("=== rebuilding vm-bootsys ===")
        extract_partition(
            "vm-bootsys",
            plan.vm_tl,
            out_fw / "vm-bootsys.img",
            plan.vm_size,
            log,
        )
    else:
        log("=== vm-bootsys ===")
        log("  skipped: OTA has no vm-bootsys.transfer.list")

    log("=== rebuilding dynamic partitions ===")
    raw_parts: dict[str, Path] = {}
    for name in sorted(plan.sizes):
        if name == "oem":
            tl = package / "oem" / f"OEM_{plan.oem_name}.transfer.list"
            target = out_fw / "oem" / f"OEM_{plan.oem_name}.img"
        elif name == "vgc":
            tl = package / "vgc" / f"VGC_{plan.vgc_name}.transfer.list"
            target = raw_dir / "vgc.img"
        else:
            tl = package / f"{name}.transfer.list"
            target = raw_dir / f"{name}.img"
        if not tl.is_file():
            raise ToolError(f"missing transfer list for {name}: {tl}")
        if not reuse_image(target, plan.sizes[name], args.reuse_raw, log):
            extract_partition(name, tl, target, plan.sizes[name], log)
        raw_parts[name] = target
    return raw_parts


def extract_oem_variants(
    package: Path,
    out_fw: Path,
    raw_parts: dict[str, Path],
    plan: SuperPlan,
    args: argparse.Namespace,
    log: Log,
) -> None:
    log("=== OEM variants ===")
    oem_dir = out_fw / "oem"
    oem_dir.mkdir(exist_ok=True)
    for variant in oem_variants(package):
        tl = package / "oem" / f"OEM_{variant}.transfer.list"
        sizes, _ = parse_op_list(package / "oem" / f"dynamic_partitions_op_list_{variant}")
        size = sizes.get("oem", 0)
        target = oem_dir / f"OEM_{variant}.img"
        if target in raw_parts.values():
            log(f"  {variant}: already extracted for super")
        elif not reuse_image(target, size, args.reuse_raw, log):
            extract_partition("oem", tl, target, size, log)
        vbmeta = package / "firmware-update" / f"vbmeta_oem_{variant}.img"
        if not vbmeta.is_file():
            raise ToolError(f"missing {vbmeta}")
        shutil.copy2(vbmeta, oem_dir / vbmeta.name)

    chosen_vbmeta = oem_dir / f"vbmeta_oem_{plan.oem_name}.img"
    if plan.oem_name != "none" and chosen_vbmeta.is_file():
        shutil.copy2(chosen_vbmeta, out_fw / "vbmeta_oem.img")

    if plan.vgc_name != "none":
        vbmeta = package / "firmware-update" / f"vbmeta_vgc_{plan.vgc_name}.img"
        if not vbmeta.is_file():
            raise ToolError(f"missing {vbmeta}")
        shutil.copy2(vbmeta, out_fw / "vbmeta_vgc.img")


def assemble_super(
    out_fw: Path,
    raw_parts: dict[str, Path],
    plan: SuperPlan,
    args: argparse.Namespace,
    log: Log,
) -> dict:
    if args.no_super:
        log("super.img skipped (--no-super)")
        return {}

    super_path = out_fw / "super.img"
    super_parts: dict = {}
    if args.reuse_super and super_path.is_file():
        try:
            candidate = lp_metadata(super_path, log)
        except ToolError as error:
            log(f"  cannot reuse super.img: {error}")
            candidate = {}
        if (
            candidate
            and probe_sparse_device_size(super_path) == plan.device_size
            and all(
                name in candidate and candidate[name]["bytes"] >= size
                for name, size in plan.sizes.items()
            )
        ):
            super_parts = candidate
            log(
                f"reused super.img: {fmt_size(super_path.stat().st_size)} sparse, "
                f"{len(super_parts)} partitions"
            )

    if not super_parts:
        log("=== building super.img ===")
        super_tmp = out_fw / "super.img.tmp"
        build_super(
            raw_parts,
            plan.sizes,
            plan.groups,
            plan.device_size,
            super_tmp,
            log,
        )
        super_parts = lp_metadata(super_tmp, log)
        expanded = probe_sparse_device_size(super_tmp)
        if expanded is not None and expanded != plan.device_size:
            raise ToolError(
                f"super.img expands to {expanded}, expected {plan.device_size}"
            )
        log(
            f"super.img: {fmt_size(super_tmp.stat().st_size)} sparse, "
            f"{len(super_parts)} partitions, {fmt_size(plan.device_size)} expanded"
        )
        super_tmp.replace(super_path)

    missing = sorted(set(plan.sizes) - set(super_parts))
    if missing:
        raise ToolError(f"super.img lacks partitions: {', '.join(missing)}")
    for name, size in plan.sizes.items():
        actual = super_parts[name]["bytes"]
        if actual < size:
            raise ToolError(
                f"super partition {name} is {actual} bytes, expected {size}"
            )
    return super_parts


def verify_chain(
    out: Path,
    out_fw: Path,
    raw_parts: dict[str, Path],
    args: argparse.Namespace,
    log: Log,
) -> dict[str, bool]:
    log("=== AVB chain verification ===")
    avb_files = {
        name: path for name, path in raw_parts.items() if name not in {"oem", "vgc"}
    }
    avb_files.update(
        {
            "boot": out_fw / "boot.img",
            "dtbo": out_fw / "dtbo.img",
            "vendor_boot": out_fw / "vendor_boot.img",
            "recovery": out_fw / "recovery.img",
            "vm-bootsys": out_fw / "vm-bootsys.img",
            "system": raw_parts.get("system", Path("/nonexistent")),
            "vendor": raw_parts.get("vendor", Path("/nonexistent")),
            "product": raw_parts.get("product", Path("/nonexistent")),
            "system_ext": raw_parts.get("system_ext", Path("/nonexistent")),
            "odm": raw_parts.get("odm", Path("/nonexistent")),
            "vendor_dlkm": raw_parts.get("vendor_dlkm", Path("/nonexistent")),
            "oem": raw_parts.get("oem", Path("/nonexistent")),
            "vgc": raw_parts.get("vgc", Path("/nonexistent")),
            "vbmeta_system": out_fw / "vbmeta_system.img",
        }
    )
    avb_targets = [("vbmeta", out_fw / "vbmeta.img")]
    if (out_fw / "vbmeta_system.img").is_file():
        avb_targets.append(("vbmeta_system", out_fw / "vbmeta_system.img"))
    if (out_fw / "vbmeta_oem.img").is_file():
        avb_targets.append(("vbmeta_oem", out_fw / "vbmeta_oem.img"))
    if (out_fw / "vbmeta_vgc.img").is_file():
        avb_targets.append(("vbmeta_vgc", out_fw / "vbmeta_vgc.img"))

    if args.skip_avb:
        log("  skipped (--skip-avb)")
        return {}

    results = verify_avb(out, avb_files, avb_targets, log)
    failed = [name for name, ok in results.items() if not ok]
    if failed:
        raise ToolError("AVB verification failed: " + ", ".join(failed))
    return results


def install_scripts(
    out: Path,
    scripts_dir: Path,
    props: dict[str, str],
    models: list[str],
    plan: SuperPlan,
    args: argparse.Namespace,
    log: Log,
) -> list[str]:
    log("=== package scripts ===")
    if args.no_super:
        log("  skipped: super.img was not built")
        return []
    if args.no_scripts:
        log("  skipped (--no-scripts)")
        return []
    script_names = copy_scripts(scripts_dir, out, log)
    write_package_env(out, props, "|".join(models), plan.oem_name, log)
    check_script_contract(out, log)
    return script_names


def _run(
    args: argparse.Namespace,
    package: Path,
    out: Path,
    display_out: str,
    scripts_dir: Path,
    log: Log,
) -> int:
    props = _load_props(package, log)
    log(f"=== {package.name} ===")
    log(f"device={props['pre-device']} build={props['post-build']}")
    log(f"output={display_out}")

    plan = plan_super(package, props, args.oem, args.vgc, args.device_size, log)
    models = model_candidates(props)
    log(f"oem variant: {plan.oem_name} ({plan.oem_note})")
    log(f"vgc variant: {plan.vgc_name} ({plan.vgc_note})")
    log(f"model candidates: {', '.join(models) if models else 'unknown'}")
    log(f"super device size: {plan.device_size} bytes ({fmt_size(plan.device_size)})")
    log(
        "super partitions: "
        + ", ".join(
            f"{name}={fmt_size(size)}" for name, size in sorted(plan.sizes.items())
        )
    )

    data_bytes = sum(plan.sizes.values())
    need = (
        data_bytes
        + int(data_bytes * 1.1)
        + plan.vm_size * 2
        + firmware_size(package) * 2
        + 512 * 1024 * 1024
    )
    free_space_check(out, need)
    log(f"disk space estimate: {fmt_size(need)}")

    if args.dry_run:
        log("dry-run: plan validated, no partition images written")
        return 0

    out_fw = out / "firmware-update"
    out_fw.mkdir(parents=True, exist_ok=True)
    raw_dir = out / ".raw"
    raw_dir.mkdir(exist_ok=True)

    log("=== copying physical images ===")
    copy_firmware(package, out_fw, log)

    raw_parts = extract_partitions(package, out_fw, raw_dir, plan, args, log)
    extract_oem_variants(package, out_fw, raw_parts, plan, args, log)

    log("=== filesystem check ===")
    fsck_targets = {
        name: raw_parts[name]
        for name in sorted(raw_parts)
        if raw_parts[name].suffix == ".img"
    }
    if args.skip_fsck:
        fsck_results: dict[str, bool] = {}
        log("  skipped (--skip-fsck)")
    else:
        fsck_results = fsck_images(fsck_targets, log)

    assemble_super(out_fw, raw_parts, plan, args, log)
    avb_results = verify_chain(out, out_fw, raw_parts, args, log)

    script_names = install_scripts(
        out, scripts_dir, props, models, plan, args, log
    )

    if not args.keep_raw:
        shutil.rmtree(raw_dir, ignore_errors=True)
    else:
        log("raw images kept in .raw/")

    build_md = out / "BUILD.md"
    build_md.write_text(
        render_build_md(
            package,
            props,
            plan.device_size,
            plan.sizes,
            plan.oem_name,
            plan.oem_note,
            plan.vgc_name,
            plan.vgc_note,
            [],  # filled after the manifest
            log,
        ),
        encoding="utf-8",
    )
    manifest = write_manifest(out, log)
    build_md.write_text(
        render_build_md(
            package,
            props,
            plan.device_size,
            plan.sizes,
            plan.oem_name,
            plan.oem_note,
            plan.vgc_name,
            plan.vgc_note,
            manifest,
            log,
        ),
        encoding="utf-8",
    )

    summary = {
        "source_package": package.name,
        "output_dir": out.name,
        "device": props["pre-device"],
        "hardware_devices": props.get("hardware-device", ""),
        "model_candidates": models,
        "build": props["post-build"],
        "version": props.get("post-version"),
        "super_device_size": plan.device_size,
        "super_partitions": plan.sizes,
        "oem_variant": plan.oem_name,
        "vgc_variant": plan.vgc_name,
        "scripts": script_names,
        "fsck": fsck_results,
        "avb": avb_results,
    }
    (out / "build.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    log("=== done ===")
    log(f"package: {display_out}")
    log(f"size: {fmt_size(sum(record['size'] for record in manifest))}")
    log(f"run: cd {display_out} && chmod +x ./*.sh && ./flash-safe.sh")
    log("full flash: ./flash.sh (writes bootloader and UFS firmware)")
    return 0
