"""只读取单份发布快照；离线构建新集合后原子切换清单，查询不加载历史目录。"""
from dataclasses import dataclass
from functools import cached_property
import hashlib
import json
from pathlib import Path
import re
import secrets

BACKEND = Path(__file__).resolve().parents[2]
CATALOG = BACKEND / "knowledge" / "published-rules.json"
INDEX_DIR = BACKEND / "data" / "chroma"
MODEL_DIR = BACKEND / "data" / "models" / "bge-small-zh-v1.5"
MODEL_ID = "BAAI/bge-small-zh-v1.5"
MODEL_REVISION = "7999e1d3359715c523056ef9478215996d62a620"
QUERY_PREFIX = "为这个句子生成表示以用于检索相关文章："
MIN_SIMILARITY = .55


class KnowledgeError(Exception):
    pass


@dataclass(frozen=True)
class Catalog:
    version: str
    digest: str
    chunks: list[dict]


def load_catalog(path: Path = CATALOG) -> Catalog:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if (not isinstance(data, dict) or data.get("status") != "published"
            or any(not isinstance(data.get(key), str) or not data[key].strip() for key in ("version", "source"))
            or not isinstance(data.get("sections"), list) or not data["sections"]):
            raise ValueError()
        chunks, identifiers = [], set()
        for section in data["sections"]:
            if (not isinstance(section, dict) or any(not isinstance(section.get(key),str) or not section[key].strip()
                                                     for key in ("id","title","text"))):
                raise ValueError()
            identifier = section["id"]
            if not re.fullmatch(r"BR\d{2}|ROLE", identifier) or identifier in identifiers:
                raise ValueError()
            identifiers.add(identifier)
            # 段落优先；超长段落按句拆分，避免Embedding静默截断业务限制。
            units = []
            for paragraph in section["text"].split("\n\n"):
                lines = paragraph.strip().splitlines()
                if len(lines) >= 3 and lines[0].startswith("|") and re.fullmatch(r"[| :\-]+",lines[1]):
                    headings = [cell.strip() for cell in lines[0].strip("|").split("|")]
                    for row in lines[2:]:
                        cells = [cell.strip() for cell in row.strip("|").split("|")]
                        if len(cells) != len(headings):
                            raise ValueError()
                        # 每行携带列名，分段后也能区分普通用户与管理员的权限。
                        units.append("；".join(f"{key}：{value}" for key,value in zip(headings,cells)) + "。")
                else:
                    units.extend(re.split(r"(?<=[。；])", paragraph) if len(paragraph) > 400 else [paragraph])
            parts, current = [], ""
            for unit in units:
                unit = unit.strip()
                if not unit:
                    continue
                if len(unit) > 450:
                    # 表格按行拆开，不截掉单行中的否定条件。
                    lines = unit.splitlines()
                    if len(lines) == 1 or any(len(line) > 450 for line in lines):
                        raise ValueError()
                    if current:
                        parts.append(current)
                        current = ""
                    parts.extend(lines)
                    continue
                if current and len(current) + len(unit) > 400:
                    parts.append(current)
                    current = ""
                current += ("\n" if current else "") + unit
            if current:
                parts.append(current)
            if not parts:
                raise ValueError()
            chunks.extend(dict(id=f"{identifier}-{i+1}", section=identifier,
                title=section["title"], version=data["version"], excerpt=part) for i, part in enumerate(parts))
        canonical = json.dumps(dict(version=data["version"], source=data["source"], chunks=chunks),
                               ensure_ascii=False, sort_keys=True).encode()
        return Catalog(data["version"], hashlib.sha256(canonical).hexdigest(), chunks)
    except (OSError, ValueError, KeyError, TypeError):
        raise KnowledgeError("catalog") from None


def download_model() -> None:
    from huggingface_hub import snapshot_download
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    snapshot_download(MODEL_ID, revision=MODEL_REVISION, local_dir=str(MODEL_DIR), token=False,
                      allow_patterns=["*.json", "*.txt", "*.safetensors"])


class RuleIndex:
    def __init__(self, catalog: Catalog, index_dir: Path = INDEX_DIR, model_dir: Path = MODEL_DIR):
        self.catalog, self.index_dir, self.model_dir = catalog, index_dir, model_dir

    @cached_property
    def encoder(self):
        if not (self.model_dir / "model.safetensors").is_file():
            raise KnowledgeError("model_missing")
        import torch
        from sentence_transformers import SentenceTransformer
        torch.set_num_threads(2)
        return SentenceTransformer(str(self.model_dir), device="cpu", local_files_only=True,
                                   trust_remote_code=False, token=False)

    @cached_property
    def client(self):
        import chromadb
        from chromadb.config import Settings
        return chromadb.PersistentClient(path=str(self.index_dir), settings=Settings(anonymized_telemetry=False))

    def embed(self, texts: list[str], *, query=False):
        inputs = [QUERY_PREFIX + text if query else text for text in texts]
        if any(len(self.encoder.tokenizer(text, truncation=False)["input_ids"]) > self.encoder.max_seq_length for text in inputs):
            raise KnowledgeError("text_length")
        return self.encoder.encode(inputs, normalize_embeddings=True, show_progress_bar=False).tolist()

    def build(self) -> dict:
        name = f"smrbs_rules_{self.catalog.digest[:12]}_{secrets.token_hex(4)}"
        collection = self.client.create_collection(name=name, embedding_function=None,
                                                   configuration={"hnsw": {"space": "cosine"}})
        chunks = self.catalog.chunks
        vectors = self.embed([c["title"] + "\n" + c["excerpt"] for c in chunks])
        collection.add(ids=[c["id"] for c in chunks], embeddings=vectors,
                       documents=[c["excerpt"] for c in chunks],
                       metadatas=[dict(release=self.catalog.digest, version=c["version"]) for c in chunks])
        if collection.count() != len(chunks):
            raise KnowledgeError("index")
        manifest = dict(collection=name, digest=self.catalog.digest, version=self.catalog.version,
                        model=MODEL_ID, revision=MODEL_REVISION, chunks=len(chunks))
        target = self.index_dir / "published.json"
        temporary = target.with_suffix(f".{secrets.token_hex(4)}.tmp")
        temporary.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(target)
        return manifest

    def retrieve(self, question: str) -> list[dict]:
        try:
            manifest = json.loads((self.index_dir / "published.json").read_text(encoding="utf-8"))
            if (manifest["digest"] != self.catalog.digest or manifest["version"] != self.catalog.version
                or manifest["model"] != MODEL_ID or manifest["revision"] != MODEL_REVISION):
                raise KnowledgeError("stale_index")
            collection = self.client.get_collection(manifest["collection"], embedding_function=None)
            found = collection.query(query_embeddings=self.embed([question], query=True), n_results=min(4,len(self.catalog.chunks)),
                where={"release": self.catalog.digest}, include=["distances", "documents"])
            allowed = {c["id"]:c for c in self.catalog.chunks}
            result = []
            for identifier, distance, document in zip(found["ids"][0], found["distances"][0], found["documents"][0]):
                if identifier not in allowed or document != allowed[identifier]["excerpt"]:
                    raise KnowledgeError("index")
                if 1 - distance >= MIN_SIMILARITY:
                    result.append(allowed[identifier])
            return result
        except KnowledgeError:
            raise
        except (OSError, ValueError, KeyError, TypeError):
            raise KnowledgeError("index") from None
