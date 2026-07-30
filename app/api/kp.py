"""
KP (Commercial Proposal) management API endpoints.

Endpoints:
- POST /api/kp/upload — Upload a KP/presentation
- GET /api/kp/list — List all KP by industry
- GET /api/kp/<id> — Get KP details
- DELETE /api/kp/<id> — Delete KP
- GET/POST /api/followup/rules — Get/set follow-up rules
"""
import json
import os
import logging
from pathlib import Path
from flask import Blueprint, request, jsonify, current_app
from middleware import require_auth
from agent.memory import VectorMemory

logger = logging.getLogger("kp_api")

kp_bp = Blueprint("kp", __name__)

# Upload directory
UPLOAD_DIR = Path(__file__).parent.parent.parent / "uploads" / "kp"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

# Allowed extensions
ALLOWED_EXTENSIONS = {".pdf", ".docx", ".doc", ".txt", ".md"}


def _get_memory() -> VectorMemory:
    from agent import get_memory
    return get_memory()


def _extract_text_from_file(file_path: str) -> str:
    """Extract text from uploaded file."""
    ext = Path(file_path).suffix.lower()

    if ext in [".txt", ".md"]:
        return Path(file_path).read_text(encoding="utf-8", errors="ignore")

    if ext == ".pdf":
        try:
            import fitz  # PyMuPDF
            doc = fitz.open(file_path)
            text = ""
            for page in doc:
                text += page.get_text()
            doc.close()
            return text
        except ImportError:
            logger.warning("PyMuPDF not installed, cannot extract PDF text")
            return ""

    if ext in [".docx", ".doc"]:
        try:
            from docx import Document
            doc = Document(file_path)
            return "\n".join([p.text for p in doc.paragraphs])
        except ImportError:
            logger.warning("python-docx not installed, cannot extract DOCX text")
            return ""

    return ""


@kp_bp.route("/api/kp/upload", methods=["POST"])
@require_auth
def upload_kp():
    """Upload a KP or presentation."""
    if "file" not in request.files:
        return jsonify({"error": "No file provided"}), 400

    file = request.files["file"]
    if not file.filename:
        return jsonify({"error": "No file selected"}), 400

    # Get metadata
    industry = request.form.get("industry", "general")
    doc_type = request.form.get("doc_type", "kp")  # kp or presentation
    description = request.form.get("description", "")

    # Validate extension
    ext = Path(file.filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        return jsonify({"error": f"Invalid file type. Allowed: {ALLOWED_EXTENSIONS}"}), 400

    # Save file
    filename = f"{industry}_{doc_type}_{file.filename}"
    file_path = UPLOAD_DIR / filename
    file.save(str(file_path))

    # Extract text
    text_content = _extract_text_from_file(str(file_path))
    if not text_content:
        text_content = description or f"Файл: {filename}"

    # Save to ChromaDB
    doc_id = f"{industry}_{doc_type}_{Path(file.filename).stem}"
    memory = _get_memory()
    memory.save_knowledge(
        doc_id=doc_id,
        content=text_content,
        doc_type=doc_type,
        industry=industry,
        metadata={
            "filename": file.filename,
            "file_path": str(file_path),
            "description": description,
            "uploaded_at": _now(),
        },
    )

    logger.info("Uploaded KP: %s (%s, %s)", filename, doc_type, industry)
    return jsonify({
        "ok": True,
        "doc_id": doc_id,
        "filename": file.filename,
        "industry": industry,
        "doc_type": doc_type,
    })


@kp_bp.route("/api/kp/list", methods=["GET"])
@require_auth
def list_kp():
    """List all KP/presentations by industry."""
    memory = _get_memory()

    # Get all knowledge documents
    industry = request.args.get("industry")
    doc_type = request.args.get("doc_type")

    if industry:
        docs = memory.get_knowledge_by_industry(industry, doc_type)
    else:
        # Get all
        col = memory._get_collection("knowledge_base")
        results = col.get()
        docs = []
        for i, doc in enumerate(results["documents"]):
            docs.append({
                "id": results["ids"][i],
                "content": doc[:200] + "..." if len(doc) > 200 else doc,
                "metadata": results["metadatas"][i] if results["metadatas"] else {},
            })

    return jsonify({"kp": docs})


@kp_bp.route("/api/kp/<doc_id>", methods=["GET"])
@require_auth
def get_kp(doc_id: str):
    """Get KP details."""
    memory = _get_memory()
    col = memory._get_collection("knowledge_base")

    try:
        result = col.get(ids=[doc_id])
        if result["documents"]:
            return jsonify({
                "id": doc_id,
                "content": result["documents"][0],
                "metadata": result["metadatas"][0] if result["metadatas"] else {},
            })
    except Exception:
        pass

    return jsonify({"error": "KP not found"}), 404


@kp_bp.route("/api/kp/<doc_id>", methods=["DELETE"])
@require_auth
def delete_kp(doc_id: str):
    """Delete a KP."""
    memory = _get_memory()

    # Get file path before deleting
    col = memory._get_collection("knowledge_base")
    try:
        result = col.get(ids=[doc_id])
        if result["metadatas"]:
            file_path = result["metadatas"][0].get("file_path")
            if file_path and Path(file_path).exists():
                Path(file_path).unlink()
    except Exception:
        pass

    memory.delete_knowledge(doc_id)
    return jsonify({"ok": True})


@kp_bp.route("/api/followup/rules", methods=["GET"])
@require_auth
def get_followup_rules():
    """Get follow-up rules."""
    cfg = current_app.config["load_config"]()
    rules = cfg.get("followup_rules", {})
    return jsonify(rules)


@kp_bp.route("/api/followup/rules", methods=["POST"])
@require_auth
def set_followup_rules():
    """Update follow-up rules."""
    data = request.get_json() or {}
    cfg = current_app.config["load_config"]()

    if "followup_rules" not in cfg:
        cfg["followup_rules"] = {}

    rules = cfg["followup_rules"]

    # Update rules
    if "interested_days" in data:
        rules["interested_days"] = int(data["interested_days"])
    if "negotiating_days" in data:
        rules["negotiating_days"] = int(data["negotiating_days"])
    if "called_days" in data:
        rules["called_days"] = int(data["called_days"])
    if "interested_message" in data:
        rules["interested_message"] = data["interested_message"]
    if "negotiating_message" in data:
        rules["negotiating_message"] = data["negotiating_message"]
    if "called_message" in data:
        rules["called_message"] = data["called_message"]
    if "interested_escalate_days" in data:
        rules["interested_escalate_days"] = int(data["interested_escalate_days"])
    if "negotiating_escalate_days" in data:
        rules["negotiating_escalate_days"] = int(data["negotiating_escalate_days"])

    cfg["followup_rules"] = rules
    current_app.config["save_config"](cfg)
    return jsonify({"ok": True})


def _now() -> str:
    from datetime import datetime
    return datetime.now().isoformat()
