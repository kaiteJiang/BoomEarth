# 鲜彩漫画逐物件手绘演示

两段各 9 秒的独立演示，画布为 1920 × 1080。保留原生插画、左文右图、信息卡和画笔动效，用于查看逐物件绘制、浅色填充、完成区域稳定性与时间轴回退。

在仓库根目录运行：

```sh
npm ci
node automation/scripts/prepare_vivid_comic_demo.mjs
npx hyperframes preview examples/vivid-comic
```

`prepare` 从已安装的 GSAP 和仓库 `video-content-template/assets/` 复制必要资源到被忽略的 `generated/`，并从语义契约生成浏览器绑定。相同内容直接复用；资源发生变化时，旧字节保存在 `generated/retained/`，不会丢弃已有版本。缺少依赖或原图哈希不匹配时会停止准备。

如需使用另一个已安装的依赖目录，可运行：

```sh
node automation/scripts/prepare_vivid_comic_demo.mjs --node-modules /path/to/node_modules
```

渲染无配音 MP4：

```sh
npx hyperframes render examples/vivid-comic --output examples/vivid-comic/renders/demo.mp4
```

过程检查使用本机 Chrome，默认路径为 Windows 标准安装位置；其他位置通过 `CHROME_PATH` 指定。每次使用新的输出目录：

```sh
node automation/scripts/qc_comic_semantic_process.mjs examples/vivid-comic .internal/vivid-comic-qc
```

公开固定输入包括：

- `assets/scene-11.png`、`assets/scene-26.png`：1672 × 941 原生插画。
- `assets/scene-11.index.png`、`assets/scene-26.index.png`：逐像素语义归属图；0 为背景，其余值对应契约中的物件顺序。
- `comic-semantic-parts.json`：绑定原图 SHA-256 的语义物件和 RLE 归属。
- `sponge-drawing-plan.json`：绘制顺序、权重和后备多边形。
- `index.html`：18 秒时间轴和展示版式。

原图、索引图和契约是这两幅插画的已审示例数据。新插画需要自己的语义归属，不能直接复用示例坐标。演示没有配音，也不包含私有生产项目、日志或机器路径。
