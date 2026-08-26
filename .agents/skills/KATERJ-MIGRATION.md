# KaterJ Skill 真源迁移

`.agents/skills/katerj-*` 是 BoomEarth 维护的正式 Skill 真源。运行时名称遵循
Agent Skills 的小写 kebab-case 规范；对外品牌写作 `katerJ`。

旧的 `ra-*`、`dbs-*`、`rn-*`、`tts-skill`、`skill-captions` 与
`ian-xiaohei-illustrations` 入口仅作为 compatibility bridge，用来承接历史交接稿、
测试、自动化命令和旧对话里的调用名。新规则只维护在 `katerj-*` 真源中。

部分旧目录仍保存 vendored 运行脚本、回归素材、字体或参考资产。迁移入口文档
不改变第三方脚本、素材或字体的原许可证，也不把它们声明为 KaterJ 原创。
具体来源、许可证和商业使用边界继续以根目录 `LICENSE-NOTES.md` 为准。

维护规则：

1. 新功能先修改对应 `katerj-*` 真源。
2. 旧入口不得重新积累长流程，只保留真源指针。
3. 稳定数据字段可以继续使用旧标签，避免破坏历史 receipt。
4. vendored 代码或素材若被替换，必须保留可验证的独立实现与许可证证据。
