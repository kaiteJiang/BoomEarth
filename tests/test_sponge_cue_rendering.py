"""Cue boundary and existing lexical-review binding integration tests."""
import hashlib
import json
from types import SimpleNamespace

import pytest

from boomearth.providers.volcengine_asr import ASRWord
from boomearth.video.artifacts import capture_regular_file
from boomearth.video.render_project import ContentRenderProjectError, _sponge_text_layers
from boomearth.video.sponge_components import SpongeComponentError, load_reviewed_cue_captions


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _layer(tmp_path, cue, *, end=4):
    path = tmp_path / 'sponge-text-layer.json'
    _write(path, dict(schema_version=2, scenes={'scene-01': dict(
        header='章节', title='标题', kicker='重点', conclusion='结论', effect='steps',
        cards=[dict(title='公开', detail='进入页面', cue=cue)],
    )}))
    return (capture_regular_file(path), SimpleNamespace(scenes=[SimpleNamespace(id='scene-01')]),
            SimpleNamespace(scenes=[SimpleNamespace(id='scene-01', start=0, end=end)]))


def test_exact_cue_needs_no_historical_review_and_uses_word_onset(tmp_path):
    def forbidden():
        pytest.fail('exact cue must not load historical review artifacts')
    args = _layer(tmp_path, '公开内容')
    result = _sponge_text_layers(*args, (dict(text='选择公开内容', start=1, end=3),),
        words=(ASRWord('选择', 1, 2), ASRWord('公开内容', 2, 3)), load_cue_reviews=forbidden)
    assert result['scene-01']['cards'][0]['cue_at'] == 2


def test_word_after_scene_end_cannot_escape_in_caption_that_starts_inside_scene(tmp_path):
    args = _layer(tmp_path, '公开内容', end=2)
    with pytest.raises(ContentRenderProjectError):
        _sponge_text_layers(*args, (dict(text='选择公开内容', start=1, end=3),),
            words=(ASRWord('选择', 1, 2), ASRWord('公开内容', 2, 3)))


def _review_fixture(tmp_path, *, bad_row=False):
    captions = (dict(text='让它归类', start=1, end=3),)
    words = (ASRWord('让', 1, 1.5), ASRWord('他', 1.5, 2), ASRWord('归类', 2, 3))
    paths = {name: tmp_path / 'media/captions' / name for name in
             ('caption-qc.json', 'captions.json', 'captions_words.json')}
    hashes = {'captions.json': _write(paths['captions.json'], captions),
              'captions_words.json': _write(paths['captions_words.json'],
                  [dict(text=w.text, start=w.start, end=w.end) for w in words])}
    row = dict(caption=1, text='让它归类', asr_span='让他归类', start=1, end=3,
               first_word=0, last_word=2, first_start_exact=True, last_end_exact=True)
    if bad_row:
        row['asr_span'] = '让他删除'
    audit_path = tmp_path / '工程/caption-final-mapping-audit-v01.json'
    audit_hash = _write(audit_path, dict(narration_sha256='locked-wav',
        all_actual_word_edges_exact=True, candidate_artifact_sha256=hashes, rows=[row]))
    review_path = tmp_path / '工程/caption-semantic-review.json'
    review_hash = _write(review_path, dict(status='pass', narration_sha256='locked-wav',
        actual_asr_lexical_differences_reviewed=True, actual_word_boundaries_reviewed=True,
        source_words_preserved=True, words_numbers_proper_names_negations_reviewed=True,
        text_or_timing_changes_after_asr=False, source_artifact_sha256=hashes,
        evidence_sha256={audit_path.name: audit_hash}))
    _write(paths['caption-qc.json'], dict(manual_semantic_review=True,
        semantic_review_path='工程/caption-semantic-review.json', semantic_review_sha256=review_hash))
    snapshots = {name: capture_regular_file(path, within=tmp_path) for name, path in paths.items()}
    return captions, words, snapshots, review_path, audit_path


def test_whole_caption_alias_requires_the_exact_existing_review_chain(tmp_path):
    captions, words, snapshots, review, audit = _review_fixture(tmp_path)
    approved, captured = load_reviewed_cue_captions(tmp_path, caption_snapshots=snapshots,
        narration_sha256='locked-wav', captions=captions, words=words)
    assert approved == frozenset({('让它归类', '让他归类', 1, 3)})
    assert [s.path for s in captured] == [review, audit]
    calls = []
    def load():
        calls.append(True)
        return approved
    args = _layer(tmp_path, '让它归类')
    result = _sponge_text_layers(*args, captions, words=words, load_cue_reviews=load)
    assert result['scene-01']['cards'][0]['cue_at'] == 1
    assert calls == [True]


@pytest.mark.parametrize('tamper', ['review-bytes', 'audit-bytes', 'current-captions', 'current-words', 'wav', 'signed-wrong-row'])
def test_lexical_review_cannot_authorize_changed_or_different_inputs(tmp_path, tamper):
    captions, words, snapshots, review, audit = _review_fixture(tmp_path, bad_row=tamper=='signed-wrong-row')
    if tamper in {'review-bytes', 'audit-bytes'}:
        with (review if tamper=='review-bytes' else audit).open('ab') as handle:
            handle.write(b'\n')
    if tamper in {'current-captions', 'current-words'}:
        name = 'captions.json' if tamper=='current-captions' else 'captions_words.json'
        with snapshots[name].path.open('ab') as handle:
            handle.write(b'\n')
        snapshots[name] = capture_regular_file(snapshots[name].path, within=tmp_path)
    with pytest.raises(SpongeComponentError, match='lexical review is invalid'):
        load_reviewed_cue_captions(tmp_path, caption_snapshots=snapshots,
            narration_sha256='other-wav' if tamper=='wav' else 'locked-wav', captions=captions, words=words)
