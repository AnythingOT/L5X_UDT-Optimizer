# app.py
import sys, os, re, uuid, threading, webbrowser, json, io, zipfile, datetime
from flask import Flask, request, jsonify, render_template, Response, stream_with_context
from L5XOpt_UDT import (
    extract_all_udt_definitions,
    optimize_and_regenerate_udt,
    detect_l5x_type,
    _topological_sort,
    _estimate_udt_size,
)
from L5XGen_UDT import generate_udt_l5x_from_tags

def resource_path(rel):
    try:    base = sys._MEIPASS
    except: base = os.path.abspath(".")
    return os.path.join(base, rel)

app = Flask(__name__,
            template_folder=resource_path("templates"),
            static_folder=resource_path("static"),
            static_url_path="/static")
_download_store: dict = {}

def sanitize_filename(name):
    return re.sub(r"[^a-zA-Z0-9_\-]", "_", name.strip())


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/preview", methods=["POST"])
def preview():
    f = request.files.get("file")
    if not f or not f.filename.lower().endswith(".l5x"):
        return jsonify({"success": False, "error": "A .l5x file is required."}), 400
    try:
        original_filename = os.path.splitext(f.filename)[0]
        content = f.read().decode("utf-8-sig")
        parsed  = extract_all_udt_definitions(content)
        if "error" in parsed:
            return jsonify({"success": False, "error": parsed["error"]}), 400

        l5x_type     = parsed["l5x_type"]
        all_udts     = parsed["udts"]
        aoi_registry = parsed.get("aoi_registry", {})
        safe_orig    = sanitize_filename(original_filename)

        if l5x_type == "single_udt":
            target = parsed["target"]
            udt    = all_udts[target]
            nested = [n for n in all_udts if n != target]
            reg    = {}
            for n in _topological_sort(all_udts):
                reg[n] = _estimate_udt_size(all_udts[n], reg, aoi_registry)
            size_before = reg.get(target, 0)
            return jsonify({
                "success":           True,
                "l5x_type":          "single_udt",
                "udt_name":          target,
                "member_count":      len(udt["members"]),
                "nested_udts":       nested,
                "aoi_count":         len(aoi_registry),
                "size_before":       size_before,
                "output_filename":   f"OptimizedUDTfile_{safe_orig}.l5x",
                "original_filename": original_filename,
            })
        else:
            reg = {}
            for n in _topological_sort(all_udts):
                reg[n] = _estimate_udt_size(all_udts[n], reg, aoi_registry)
            udt_list = [
                {"name": n, "member_count": len(u["members"]), "size_before": reg.get(n, 0)}
                for n, u in all_udts.items()
            ]
            return jsonify({
                "success":           True,
                "l5x_type":          "full_program",
                "udt_count":         len(all_udts),
                "aoi_count":         len(aoi_registry),
                "udts":              udt_list,
                "output_filename":   f"OptimizedProgramfile_UDTsonly_{safe_orig}.l5x",
                "original_filename": original_filename,
            })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/upload_single", methods=["POST"])
def upload_single():
    f = request.files.get("file")
    if not f or not f.filename.lower().endswith(".l5x"):
        return jsonify({"success": False, "error": "A .l5x file is required."}), 400
    try:
        original_filename = os.path.splitext(f.filename)[0]
        content = f.read().decode("utf-8-sig")
        parsed  = extract_all_udt_definitions(content)
        if "error" in parsed:
            return jsonify({"success": False, "error": parsed["error"]}), 400

        all_udts        = parsed["udts"]
        aoi_registry    = parsed.get("aoi_registry", {})
        aoi_context_xml = parsed.get("aoi_context_xml")
        target          = parsed["target"]

        reg = {}
        for n in _topological_sort(all_udts):
            reg[n] = _estimate_udt_size(all_udts[n], reg, aoi_registry)

        result = optimize_and_regenerate_udt(
            all_udts[target], all_udts=all_udts,
            udt_size_registry=reg, aoi_registry=aoi_registry,
            aoi_context_xml=aoi_context_xml,
            embed_nested_context=True,
        )
        if not result.get("success"):
            return jsonify({
                "success":             False,
                "error":               result.get("error"),
                "optimization_needed": result.get("optimization_needed"),
                "size_before":         result.get("size_before", 0),
                "size_after":          result.get("size_after", 0),
                "skipped_types":       result.get("skipped_types", []),
                "skipped_members":     result.get("skipped_members", []),
                "aoi_members":         result.get("aoi_members", []),
            }), 400

        safe_orig   = sanitize_filename(original_filename)
        filename    = f"OptimizedUDTfile_{safe_orig}.l5x"
        opt_flag    = result.get("optimization_needed")
        size_before = result.get("size_before", 0)
        size_after  = result.get("size_after", 0)

        resp = Response(result["udt_text"], mimetype="application/octet-stream")
        resp.headers.set("Content-Disposition", f'attachment; filename="{filename}"')
        resp.headers.set("X-Optimization-Needed",
                         "na" if opt_flag is None else ("true" if opt_flag else "false"))
        resp.headers.set("X-Size-Before", str(size_before))
        resp.headers.set("X-Size-After",  str(size_after))
        return resp

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/upload_program", methods=["POST"])
def upload_program():
    f = request.files.get("file")
    if not f or not f.filename.lower().endswith(".l5x"):
        return jsonify({"success": False, "error": "A .l5x file is required."}), 400
    try:
        original_filename = os.path.splitext(f.filename)[0]
        content  = f.read().decode("utf-8-sig")
        parsed   = extract_all_udt_definitions(content)
        if "error" in parsed:
            return jsonify({"success": False, "error": parsed["error"]}), 400

        session_id = str(uuid.uuid4())
        safe_orig  = sanitize_filename(original_filename)
        _download_store[session_id] = {
            "content":           content,
            "parsed":            parsed,
            "status":            "pending",
            "output_filename":   f"OptimizedProgramfile_UDTsonly_{safe_orig}.l5x",
            "original_filename": original_filename,
        }
        return jsonify({"success": True, "session_id": session_id})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/stream/<session_id>")
def stream(session_id):
    session = _download_store.get(session_id)
    if not session:
        return jsonify({"error": "Invalid session."}), 404

    def generate():
        parsed          = session["parsed"]
        all_udts        = parsed["udts"]
        aoi_registry    = parsed.get("aoi_registry", {})
        output_filename = session.get("output_filename", "OptimizedProgramfile_UDTsonly_Program.l5x")
        orig_filename   = session.get("original_filename", "Program")

        reg = {}
        for n in _topological_sort(all_udts):
            reg[n] = _estimate_udt_size(all_udts[n], reg, aoi_registry)

        order         = _topological_sort(all_udts)
        optimized_map = {}   # Done + No-Change — for full program rebuild
        changed_map   = {}   # Done ONLY — for individual ZIP
        total         = len(order)
        total_saved   = 0

        def sse(data: dict) -> str:
            return f"data: {json.dumps(data)}\n\n"

        yield sse({"type": "start", "total": total})

        for idx, name in enumerate(order):
            udt = all_udts[name]
            mc  = len(udt["members"])

            yield sse({"type": "progress", "name": name,
                        "member_count": mc, "index": idx + 1,
                        "status": "processing",
                        "size_before": reg.get(name, 0), "size_after": reg.get(name, 0)})

            result = optimize_and_regenerate_udt(
                udt, all_udts=all_udts,
                udt_size_registry=reg, aoi_registry=aoi_registry,
                aoi_context_xml=None,   # full-program: AOIs remain in the source file
                embed_nested_context=True,  # makes the per-UDT ZIP files self-contained + optimized
            )
            ok            = result.get("success", False)
            opt_needed    = result.get("optimization_needed")
            skipped_types = result.get("skipped_types", [])
            skipped_mems  = result.get("skipped_members", [])
            aoi_members   = result.get("aoi_members", [])
            size_before   = result.get("size_before", reg.get(name, 0))
            size_after    = result.get("size_after",  size_before)

            if ok:
                optimized_map[name] = result["udt_text"]
                if opt_needed:
                    display_status = "done"
                    changed_map[name] = {
                        "udt_text":    result["udt_text"],
                        "size_before": size_before,
                        "size_after":  size_after,
                    }
                    total_saved += max(0, size_before - size_after)
                else:
                    display_status = "ok"
            elif opt_needed is None:
                display_status = "na"
            else:
                display_status = "error"

            yield sse({
                "type":            "progress",
                "name":            name,
                "member_count":    mc,
                "index":           idx + 1,
                "status":          display_status,
                "size_before":     size_before,
                "size_after":      size_after,
                "skipped_types":   skipped_types,
                "skipped_members": skipped_mems,
                "aoi_members":     aoi_members,
                "error":           result.get("error", "") if display_status == "error" else "",
            })

        try:
            from L5XOpt_UDT import _replace_datatype_blocks, _extract_datatype_block
            xml_map = {}
            for name, l5x_text in optimized_map.items():
                block = _extract_datatype_block(l5x_text)
                if block:
                    xml_map[name] = block

            modified   = _replace_datatype_blocks(session["content"], xml_map, all_udts)
            prog_token = str(uuid.uuid4())
            _download_store[prog_token] = {"l5x_text": modified, "filename": output_filename}

            zip_token = str(uuid.uuid4())
            _download_store[zip_token] = {
                "type":          "changed_zip",
                "changed_map":   changed_map,
                "orig_filename": orig_filename,
                "total_saved":   total_saved,
            }

            yield sse({
                "type":        "done",
                "token":       prog_token,
                "zip_token":   zip_token,
                "succeeded":   len(optimized_map),
                "changed":     len(changed_map),
                "total":       total,
                "total_saved": total_saved,
                "filename":    output_filename,
            })
        except Exception as e:
            yield sse({"type": "error", "message": str(e)})

        _download_store.pop(session_id, None)

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.route("/download/<token>")
def download(token):
    entry = _download_store.pop(token, None)
    if not entry or "l5x_text" not in entry:
        return jsonify({"error": "Download token invalid or expired."}), 404
    resp = Response(entry["l5x_text"], mimetype="application/octet-stream")
    resp.headers.set("Content-Disposition", f'attachment; filename="{entry["filename"]}"')
    return resp


@app.route("/download_zip/<token>")
def download_zip(token):
    """ZIP of Done-only UDTs as individual L5X files + CHANGES.txt manifest."""
    entry = _download_store.pop(token, None)
    if not entry or entry.get("type") != "changed_zip":
        return jsonify({"error": "ZIP token invalid or expired."}), 404

    changed_map   = entry["changed_map"]
    orig_filename = entry.get("orig_filename", "Program")
    total_saved   = entry.get("total_saved", 0)

    buf     = io.BytesIO()
    now_str = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    manifest_lines = [
        "UDT Optimizer — Changed UDTs Only",
        f"Source : {orig_filename}.L5X",
        f"Date   : {now_str}",
        f"Saved  : {total_saved} bytes (estimated, alignment-aware)",
        "",
        f"{'UDT Name':<40} {'Before':>8} {'After':>8} {'Saved':>8}",
        "-" * 68,
    ]

    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name in sorted(changed_map):
            info   = changed_map[name]
            fname  = f"OptimizedUDTfile_{sanitize_filename(name)}.l5x"
            before = info["size_before"]
            after  = info["size_after"]
            saved  = max(0, before - after)
            manifest_lines.append(f"{name:<40} {before:>7}B {after:>7}B {saved:>7}B")
            zf.writestr(fname, info["udt_text"])

        manifest_lines += [
            "",
            "Notes:",
            "  Each file is a standalone single-UDT L5X (TargetType=DataType).",
            "  Import into Studio 5000 via: right-click DataTypes > Import DataType.",
            "  Only UDTs where member order/packing changed are included here.",
            "  AOI-typed members are preserved in the output (sorted with complex types).",
        ]
        zf.writestr("CHANGES.txt", "\n".join(manifest_lines))

    buf.seek(0)
    zip_filename = f"ChangedUDTs_{sanitize_filename(orig_filename)}.zip"
    resp = Response(buf.read(), mimetype="application/zip")
    resp.headers.set("Content-Disposition", f'attachment; filename="{zip_filename}"')
    return resp


if __name__ == "__main__":
    threading.Timer(1, lambda: webbrowser.open_new("http://127.0.0.1:5001")).start()
    app.run(debug=False, port=5001, threaded=True)
