import base64
import copy
import hashlib
import struct
from types import SimpleNamespace

import pytest
from PIL import Image

from boomearth.video.comic_semantic_parts import validate_comic_semantic_parts, build_comic_semantic_entry
from boomearth.video.sponge_components import SpongeComponentError


def fixture(tmp_path):
    source = tmp_path / "native.png"
    image=Image.new("RGB", (12, 10), "white")
    image.putpixel((6,2),(220,230,240));image.putpixel((0,5),(250,200,20));image.putpixel((6,7),(20,30,40))
    image.save(source)
    parts = [dict(id=kind, kind=kind, weight=1.0) for kind in ("character", "object", "text")]
    sha = hashlib.sha256(source.read_bytes()).hexdigest()
    drawing = {"scene-01": dict(source_sha256=sha, parts=parts)}
    runs = [(30, 0), (30, 1), (30, 2), (30, 3)]
    entry = dict(source_sha256=sha, width=12, height=10, parts=parts,
                 ownership_rle=base64.b64encode(b"".join(struct.pack("<IB", *r) for r in runs)).decode())
    value = dict(schema_version=1, scenes={"scene-01": entry})
    plan = SimpleNamespace(scenes=[SimpleNamespace(id="scene-01", visual_asset="native.png")])
    return value, drawing, plan


def test_lossless_partition_bound_to_native_dimensions_and_drawing_order(tmp_path):
    value, drawing, plan = fixture(tmp_path)
    assert validate_comic_semantic_parts(value, drawing, plan, tmp_path) == value["scenes"]


@pytest.mark.parametrize("failure", ["stale-source", "dimensions", "order", "truncated", "oversized-run", "missing-owner", "invalid-owner"])
def test_reject_corrupt_or_stale_partitions(tmp_path, failure):
    value, drawing, plan = fixture(tmp_path)
    value = copy.deepcopy(value)
    e = value["scenes"]["scene-01"]
    if failure == "stale-source": e["source_sha256"] = "0" * 64
    if failure == "dimensions": e["width"] = 13
    if failure == "order": e["parts"].reverse()
    if failure == "truncated": e["ownership_rle"] = e["ownership_rle"][:-4]
    if failure in {"oversized-run", "missing-owner", "invalid-owner"}:
        runs = {"oversized-run": [(121, 1)], "missing-owner": [(120, 1)], "invalid-owner": [(120, 4)]}[failure]
        e["ownership_rle"] = base64.b64encode(b"".join(struct.pack("<IB", *r) for r in runs)).decode()
    with pytest.raises(SpongeComponentError):
        validate_comic_semantic_parts(value, drawing, plan, tmp_path)


def test_builder_rejects_a_visible_hole_and_preserves_independent_owners(tmp_path):
    source=tmp_path/'source.png'; mask=tmp_path/'mask.png'
    image=Image.new('RGB',(30,20),'white'); owners=Image.new('L',image.size,0)
    for owner,(x,colour) in enumerate([(2,(230,242,249)),(12,(255,160,20)),(22,(15,20,30))],1):
        for yy in range(5,15):
            for xx in range(x,x+5):image.putpixel((xx,yy),colour);owners.putpixel((xx,yy),owner)
    image.save(source);owners.save(mask)
    parts=[dict(id=k,kind=k,weight=1) for k in ('character','object','text')]
    drawing,semantic,receipt=build_comic_semantic_entry(source,mask,parts)
    assert receipt['missing_visible_pixels']==0
    decoded=[]
    for count,owner in struct.iter_unpack('<IB',base64.b64decode(semantic['ownership_rle'])):decoded.extend([owner]*count)
    assert bytes(decoded)==owners.tobytes()
    assert [p['id'] for p in drawing['parts']]==['character','object','text']
    owners.putpixel((3,7),0);owners.save(mask)
    with pytest.raises(SpongeComponentError,match='omits visible'):
        build_comic_semantic_entry(source,mask,parts)


def test_existing_sidecar_cannot_hide_visible_pixels_behind_background_owner(tmp_path):
    value,drawing,plan=fixture(tmp_path)
    e=value['scenes']['scene-01']
    e['ownership_rle']=base64.b64encode(b''.join(struct.pack('<IB',*r) for r in [(1,1),(1,2),(1,3),(117,0)])).decode()
    with pytest.raises(SpongeComponentError,match='omits visible'):
        validate_comic_semantic_parts(value,drawing,plan,tmp_path)


def test_translucent_shadow_is_visible_on_white_and_requires_an_owner(tmp_path):
    value,drawing,plan=fixture(tmp_path)
    source=tmp_path/'native.png'
    with Image.open(source) as original:image=original.convert('RGBA')
    image.putpixel((0,0),(0,0,0,100));image.save(source)
    sha=hashlib.sha256(source.read_bytes()).hexdigest()
    value['scenes']['scene-01']['source_sha256']=sha;drawing['scene-01']['source_sha256']=sha
    with pytest.raises(SpongeComponentError,match='omits visible'):
        validate_comic_semantic_parts(value,drawing,plan,tmp_path)
