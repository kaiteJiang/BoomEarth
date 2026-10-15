import copy
import hashlib
from types import SimpleNamespace

import pytest

from boomearth.video.sponge_components import (
    SpongeComponentError, SpongeCueReviewRequired, resolve_cue_start, validate_drawing_plan,
)
from boomearth.providers.volcengine_asr import ASRWord


def fixture_plan(tmp_path):
    (tmp_path / 'image.png').write_bytes(b'local-native-image')
    scene = SimpleNamespace(id='scene-01', visual_asset='image.png', overlay_labels=['去做','检查'])
    parts = [dict(id=kind + str(i), kind=kind, weight=1.0,
                  polygons=[[[.1,.1],[.3,.1],[.3,.5],[.1,.5]]])
             for i,kind in enumerate(['character','object','text','text'])]
    value = dict(schema_version=2,scenes={'scene-01':dict(source_sha256=hashlib.sha256(b'local-native-image').hexdigest(),parts=parts)})
    return SimpleNamespace(scenes=[scene]), value


def test_regions_bind_native_asset_and_keep_character_first(tmp_path):
    plan,value=fixture_plan(tmp_path)
    assert validate_drawing_plan(value,plan,tmp_path)==value['scenes']
    (tmp_path/'image.png').write_bytes(b'replaced-image')
    with pytest.raises(SpongeComponentError): validate_drawing_plan(value,plan,tmp_path)


@pytest.mark.parametrize('change',['nonfinite','body-after-prop','wrong-label-count','text-before-object','zero-area','unknown-scene'])
def test_invalid_region_contract_cannot_reach_renderer(tmp_path,change):
    plan,value=fixture_plan(tmp_path); parts=value['scenes']['scene-01']['parts']
    if change=='nonfinite': parts[0]['polygons'][0][0][0]=float('nan')
    if change=='body-after-prop': parts[0],parts[1]=parts[1],parts[0]
    if change=='wrong-label-count': parts.pop()
    if change=='text-before-object': parts[1],parts[2]=parts[2],parts[1]
    if change=='zero-area': parts[1]['polygons']=[[[.2,.2],[.2,.2],[.2,.2]]]
    if change=='unknown-scene': value['scenes']['scene-02']=copy.deepcopy(value['scenes']['scene-01'])
    with pytest.raises(SpongeComponentError): validate_drawing_plan(value,plan,tmp_path)


def test_complete_speech_phrase_can_cross_caption_boundary():
    captions=[dict(text='看到一份提示词',start=2.6,end=4.9),dict(text='先收藏',start=5.04,end=5.9)]
    words=tuple(ASRWord(*row) for row in [('看到',2.6,3.1),('一份',3.1,3.9),('提示词',3.9,4.9),('先',5.04,5.3),('收藏',5.3,5.9)])
    assert resolve_cue_start('提示词，先收藏',captions,words=words)==3.9
    assert resolve_cue_start('先收藏',captions,words=words)==5.04
    with pytest.raises(SpongeComponentError): resolve_cue_start('先收藏视频',captions,words=words)
    with pytest.raises(SpongeComponentError): resolve_cue_start('提示词',captions,after=4,words=words)


def two_action_words():
    text='先整理文件再保存清单'
    words=tuple(ASRWord(character, 1 + index / 4, 1 + (index + 1) / 4)
                for index,character in enumerate(text))
    return dict(text=text,start=1,end=3.5),words


def test_two_complete_phrases_in_one_caption_have_independent_real_word_onsets():
    caption,words=two_action_words()
    first=resolve_cue_start('整理文件',[caption],words=words)
    second=resolve_cue_start('保存清单',[caption],after=first,words=words)
    assert first==1.25
    assert second==2.5
    with pytest.raises(SpongeComponentError):
        resolve_cue_start('保存清单',[caption],after=second,words=words)


def test_repeated_phrase_selects_only_a_strictly_later_real_occurrence():
    captions=[dict(text='先检查再检查',start=1,end=3)]
    words=tuple(ASRWord(*row) for row in [('先',1,1.2),('检查',1.2,1.8),('再',1.8,2),('检查',2,3)])
    assert resolve_cue_start('检查',captions,words=words)==1.2
    assert resolve_cue_start('检查',captions,after=1.2,words=words)==2


def test_reviewed_display_spelling_preserves_actual_asr_token_time():
    captions=[dict(text='AlphaTool 用来整理清单',start=1,end=4)]
    words=tuple(ASRWord(*row) for row in [('AlfaTool',1,2),('用来',2,2.5),('整理',2.5,3),('清单',3,4)])
    with pytest.raises(SpongeComponentError):
        resolve_cue_start('AlphaTool 用来整理',captions,words=words)
    with pytest.raises(SpongeCueReviewRequired):
        resolve_cue_start(captions[0]['text'],captions,words=words)
    reviewed=frozenset({('AlphaTool 用来整理清单','alfatool用来整理清单',1,4)})
    assert resolve_cue_start(captions[0]['text'],captions,words=words,reviewed_captions=reviewed)==1
    assert resolve_cue_start('清单',captions,words=words)==3
    assert captions[0]['text']=='AlphaTool 用来整理清单'


@pytest.mark.parametrize('words',[
    (),
    (ASRWord('公开内容',1,2),),
    (ASRWord('选择公开内容',.9,2),),
    (ASRWord('选择公开内容',1,2.1),),
    (ASRWord('选择',1,1.5),ASRWord('公开内容',1.4,2)),
])
def test_missing_onset_or_invalid_word_intervals_never_fall_back_to_caption_start(words):
    with pytest.raises(SpongeComponentError):
        resolve_cue_start('选择公开内容',[dict(text='选择公开内容',start=1,end=2)],words=words)


def test_two_cues_inside_one_asr_token_cannot_invent_subtoken_times():
    captions=[dict(text='选择公开内容',start=1,end=2)]
    words=(ASRWord('选择公开内容',1,2),)
    first=resolve_cue_start('选择公开',captions,words=words)
    with pytest.raises(SpongeComponentError):
        resolve_cue_start('公开内容',captions,after=first,words=words)


@pytest.mark.parametrize('prefix',['','很长相同前缀'*10])
def test_reordered_actions_cannot_bind_to_the_later_wrong_occurrence(prefix):
    words=tuple(ASRWord(*row) for row in [(prefix+'打开网站',1,2),('再',2,2.2),('打开页面',2.2,3)])
    with pytest.raises(SpongeComponentError):
        resolve_cue_start('打开页面',[dict(text=prefix+'打开页面再打开网站',start=1,end=3)],words=words)


def test_exact_first_word_cannot_authorize_a_missing_phrase_body():
    words=(ASRWord('选择',1,2),ASRWord('完全不同',2,3))
    with pytest.raises(SpongeComponentError):
        resolve_cue_start('选择公开内容',[dict(text='选择公开内容',start=1,end=3)],words=words)


def test_unreviewed_alias_before_repeated_term_cannot_shift_to_later_term():
    words=(ASRWord('诶比西',1,2),ASRWord('ABC',2,3))
    with pytest.raises(SpongeComponentError):
        resolve_cue_start('ABC',[dict(text='ABC，ABC',start=1,end=3)],words=words)
