import copy
import hashlib
from types import SimpleNamespace

import pytest

from boomearth.video.sponge_components import (
    SpongeComponentError, resolve_cue_start, validate_drawing_plan,
)


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
    captions=[dict(text='看到一个新工具',start=1.03,end=2.5),dict(text='先注册；看到一份提示词',start=2.6,end=4.9),dict(text='先收藏',start=5.04,end=5.9)]
    assert resolve_cue_start('提示词，先收藏',captions)==2.6
    assert resolve_cue_start('先收藏',captions)==5.04
    with pytest.raises(SpongeComponentError): resolve_cue_start('先收藏视频',captions)
    with pytest.raises(SpongeComponentError): resolve_cue_start('新工具',captions,after=3)
