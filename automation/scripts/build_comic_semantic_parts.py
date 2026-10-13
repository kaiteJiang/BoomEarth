"""Compile reviewed source/index-mask pairs into reproducible comic contracts."""
from pathlib import Path
import argparse
import json
import sys

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'src'))
from boomearth.video.comic_semantic_parts import build_comic_semantic_entry


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project-root',type=Path,required=True)
    parser.add_argument('--spec',type=Path,required=True)
    parser.add_argument('--output-dir',type=Path,required=True)
    args=parser.parse_args();root=args.project_root.resolve();target=args.output_dir.resolve()
    if not target.is_relative_to(root) or target.exists():
        parser.error('output must be a new directory inside the project')
    spec=json.loads(args.spec.read_text('utf8'))
    drawing={'schema_version':2,'scenes':{}};semantic={'schema_version':1,'scenes':{}};receipts=[]
    for scene in spec['scenes']:
        source=(root/scene['source']).resolve();mask=(root/scene['mask']).resolve()
        if not source.is_relative_to(root) or not mask.is_relative_to(root):
            parser.error('source and mask must remain inside the project')
        sid=scene['id']
        if sid in drawing['scenes']:parser.error('duplicate scene')
        d,s,r=build_comic_semantic_entry(source,mask,scene['parts'])
        drawing['scenes'][sid]=d;semantic['scenes'][sid]=s;receipts.append(dict(r,scene_id=sid))
    target.mkdir(parents=True,exist_ok=False)
    for name,value in [('sponge-drawing-plan.json',drawing),('comic-semantic-parts.json',semantic),('partition-build-receipt.json',{'status':'built-awaiting-semantic-and-process-review','scenes':receipts})]:
        (target/name).write_text(json.dumps(value,ensure_ascii=False,separators=(',',':')),'utf8')
    print(json.dumps({'status':'built','scenes':len(receipts),'output_dir':str(target)},ensure_ascii=False))


if __name__=='__main__':main()
