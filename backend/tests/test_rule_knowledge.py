"""已发布知识与来源校验；全为本地快照和模拟索引，不加载模型或访问网络。"""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

from pydantic import ValidationError
import pytest

from app.assistant.knowledge import (
    CATALOG, MODEL_ID, MODEL_REVISION, QUERY_PREFIX,
    KnowledgeError, RuleIndex, load_catalog,
)
from app.assistant.model import AIConnectionError
from app.assistant.rules_worker import NO_EVIDENCE, assemble_answer


def published_data():
    return json.loads(CATALOG.read_text(encoding="utf-8"))


def snapshot(tmp_path: Path, data=None):
    path = tmp_path / "published-rules.json"
    path.write_text(json.dumps(data if data is not None else published_data(), ensure_ascii=False), encoding="utf-8")
    return path


def reply(*, supported=True, ids=None, answer="开始前可以取消本人预约。"):
    return json.dumps(dict(answer=answer, supported=supported, evidence_ids=ids or []), ensure_ascii=False)


class FakeVectors(list):
    def tolist(self):
        return list(self)


class FakeEncoder:
    max_seq_length = 512

    def __init__(self):
        self.encoded = []

    def tokenizer(self, text, truncation=False):
        assert truncation is False
        return {"input_ids": list(range(len(text)))}

    def encode(self, texts, **options):
        self.encoded.append((texts, options))
        return FakeVectors([[.1, .2] for _ in texts])


def isolated_index(tmp_path):
    catalog = load_catalog(snapshot(tmp_path))
    index_dir = tmp_path / "index"
    index_dir.mkdir()
    index = RuleIndex(catalog, index_dir=index_dir, model_dir=tmp_path / "absent-model")
    encoder = FakeEncoder()
    index.encoder = encoder
    manifest = dict(collection="only_current_release", digest=catalog.digest, version=catalog.version,
                    model=MODEL_ID, revision=MODEL_REVISION, chunks=len(catalog.chunks))
    manifest_path = index_dir / "published.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return index, encoder, manifest, manifest_path


def test_published_snapshot_covers_all_current_rules_and_stable_sources(tmp_path):
    catalog = load_catalog(snapshot(tmp_path))
    assert catalog.version == "V2.0-r6+20261111"
    assert len(catalog.chunks) >= 17
    assert {c["section"] for c in catalog.chunks} == {"ROLE", *(f"BR{i:02}" for i in range(1,17))}
    assert len({c["id"] for c in catalog.chunks}) == len(catalog.chunks)
    assert all(c["excerpt"].strip() and c["title"].strip() and c["version"] == catalog.version for c in catalog.chunks)
    assert all(len(c["excerpt"]) <= 450 for c in catalog.chunks)


def test_permission_table_chunks_retain_role_column_meaning(tmp_path):
    catalog=load_catalog(snapshot(tmp_path))
    assert any('操作或信息：修改、找回或重置密码；普通用户：不可；管理员：不可，重置功能已取消。'
               in c['excerpt'] for c in catalog.chunks if c['section']=='ROLE')


def test_version_or_actual_rule_content_change_invalidates_digest(tmp_path):
    original = published_data()
    before = load_catalog(snapshot(tmp_path, original))
    assert before.digest == load_catalog(snapshot(tmp_path, deepcopy(original))).digest
    edited = deepcopy(original)
    edited["version"] = "V2.0-next"
    assert load_catalog(snapshot(tmp_path, edited)).digest != before.digest
    edited = deepcopy(original)
    edited["sections"][0]["text"] += "\n\n测试用规则修订。"
    assert load_catalog(snapshot(tmp_path, edited)).digest != before.digest


def test_catalog_rejects_nonpublished_duplicate_section_or_malformed_json(tmp_path):
    for mutate in (
        lambda data: data.update(status="draft"),
        lambda data: data["sections"].append(deepcopy(data["sections"][0])),
    ):
        data = published_data()
        mutate(data)
        with pytest.raises(KnowledgeError, match="catalog"):
            load_catalog(snapshot(tmp_path, data))
    path = tmp_path / "broken.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(KnowledgeError, match="catalog"):
        load_catalog(path)


def test_catalog_rejects_wrong_version_text_or_section_types(tmp_path):
    malformed = []
    data = published_data()
    data["version"] = 6
    malformed.append(data)
    data = published_data()
    data["sections"][0]["text"] = None
    malformed.append(data)
    data = published_data()
    data["sections"][0] = "not a section object"
    malformed.append(data)
    for data in malformed:
        with pytest.raises(KnowledgeError, match="catalog"):
            load_catalog(snapshot(tmp_path,data))


def test_retrieve_opens_only_manifest_collection_filters_current_release_and_uses_catalog_sources(tmp_path):
    index, encoder, manifest, _ = isolated_index(tmp_path)
    first, second = index.catalog.chunks[:2]
    lookups, queries = [], []
    def query(**kwargs):
        queries.append(kwargs)
        return dict(ids=[[first["id"],second["id"]]], distances=[[.1,.8]],
                    documents=[[first["excerpt"],second["excerpt"]]])
    def get_collection(name, **kwargs):
        lookups.append((name,kwargs))
        return SimpleNamespace(query=query)
    index.client = SimpleNamespace(get_collection=get_collection)
    found = index.retrieve("可以预约多久？")
    assert found == [first]
    assert lookups == [(manifest["collection"], {"embedding_function": None})]
    assert queries[0]["where"] == {"release": index.catalog.digest}
    assert encoder.encoded[0][0] == [QUERY_PREFIX + "可以预约多久？"]
    assert encoder.encoded[0][1]["normalize_embeddings"] is True


def test_stale_manifest_is_rejected_before_opening_collection_or_embedding(tmp_path):
    index, encoder, manifest, path = isolated_index(tmp_path)
    def forbidden(*args,**kwargs):
        pytest.fail("陈旧清单不得触碰集合或运行Embedding")
    index.client = SimpleNamespace(get_collection=forbidden)
    for key in ("digest", "version", "model", "revision"):
        stale = {**manifest,key:"stale"}
        path.write_text(json.dumps(stale),encoding="utf-8")
        with pytest.raises(KnowledgeError,match="stale_index"):
            index.retrieve("当前规则？")
    assert encoder.encoded == []


def test_retrieved_unknown_ids_or_modified_documents_are_rejected(tmp_path):
    index, _, _, _ = isolated_index(tmp_path)
    chunk = index.catalog.chunks[0]
    for identifier, document in (("old-release-unknown",chunk["excerpt"]),(chunk["id"],"被替换的片段")):
        collection = SimpleNamespace(query=lambda **kwargs: dict(ids=[[identifier]],distances=[[.01]],documents=[[document]]))
        index.client = SimpleNamespace(get_collection=lambda *args,**kwargs: collection)
        with pytest.raises(KnowledgeError,match="index"):
            index.retrieve("当前规则？")


def test_successful_build_publishes_complete_index_manifest(tmp_path):
    index, _, _, path = isolated_index(tmp_path)
    additions, created = [], []
    collection = SimpleNamespace(add=lambda **kwargs: additions.append(kwargs), count=lambda:len(index.catalog.chunks))
    def create_collection(**kwargs):
        created.append(kwargs)
        return collection
    index.client = SimpleNamespace(create_collection=create_collection)
    result = index.build()
    assert json.loads(path.read_text(encoding="utf-8")) == result
    assert result["collection"] == created[0]["name"]
    assert result["digest"] == index.catalog.digest and result["chunks"] == len(index.catalog.chunks)
    assert len(additions[0]["ids"]) == len(index.catalog.chunks)
    assert all(metadata["release"] == index.catalog.digest for metadata in additions[0]["metadatas"])


def test_incomplete_build_keeps_previous_published_manifest(tmp_path):
    index, _, _, path = isolated_index(tmp_path)
    before = path.read_bytes()
    collection = SimpleNamespace(add=lambda **kwargs:None, count=lambda:0)
    index.client = SimpleNamespace(create_collection=lambda **kwargs:collection)
    with pytest.raises(KnowledgeError,match="index"):
        index.build()
    assert path.read_bytes() == before


def test_answer_uses_only_real_retrieved_sources_and_deduplicates_citations(tmp_path):
    chunks = load_catalog(snapshot(tmp_path)).chunks[:2]
    result = assemble_answer(reply(ids=[chunks[1]["id"],chunks[0]["id"],chunks[1]["id"]]),chunks,"V2.0-r6")
    assert result.grounded is True
    assert [source.model_dump() for source in result.sources] == [chunks[1],chunks[0]]
    assert result.knowledge_version == "V2.0-r6"


def test_supported_answer_rejects_forged_or_empty_evidence(tmp_path):
    chunks = load_catalog(snapshot(tmp_path)).chunks[:1]
    for identifiers in ([],["invented-rule"],[chunks[0]["id"],"invented-rule"]):
        with pytest.raises(AIConnectionError,match="invalid_answer"):
            assemble_answer(reply(ids=identifiers),chunks,"V2.0-r6")


def test_unsupported_answer_discards_model_text_and_all_sources(tmp_path):
    chunks = load_catalog(snapshot(tmp_path)).chunks[:1]
    result = assemble_answer(reply(supported=False,ids=["invented-rule"],answer="伪造的天气与业务结果"),chunks,"V2.0-r6")
    assert result.answer == NO_EVIDENCE
    assert result.grounded is False and result.sources == []
    assert result.knowledge_version == "V2.0-r6"


def test_malformed_model_json_or_wrong_types_are_not_accepted(tmp_path):
    chunks = load_catalog(snapshot(tmp_path)).chunks[:1]
    for raw in ("not JSON", '[]', '{"answer":"声称成功","supported":"true","evidence_ids":[]}',
                '{"answer":"声称成功","supported":true,"evidence_ids":[],"tool":"approve"}'):
        with pytest.raises(ValidationError):
            assemble_answer(raw,chunks,"V2.0-r6")
