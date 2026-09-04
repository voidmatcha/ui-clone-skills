<h1 align="center">UI Clone Skills</h1>

<p align="center">
  <strong>复刻网站的动态表现，而不只是静态外观。</strong>
</p>

<p align="center">
  <a href="#skills"><img alt="Agent Skills" src="https://img.shields.io/badge/Agent_Skills-3-1FC07C?style=flat-square&amp;labelColor=black"></a>
  <a href="https://claude.com/product/claude-code"><img alt="Claude Code" src="https://img.shields.io/badge/Claude_Code-compatible-D97757?style=flat-square&amp;labelColor=black&amp;logo=anthropic&amp;logoColor=white"></a>
  <a href="https://github.com/openai/codex"><img alt="Codex" src="https://img.shields.io/badge/Codex-compatible-412991?style=flat-square&amp;labelColor=black&amp;logo=openai&amp;logoColor=white"></a>
  <a href="#what-it-recovers"><img alt="Input" src="https://img.shields.io/badge/input-live_URL-2EAD33?style=flat-square&amp;labelColor=black"></a>
  <a href="https://github.com/voidmatcha/ui-clone-skills/actions/workflows/ci.yml"><img alt="CI" src="https://img.shields.io/github/actions/workflow/status/voidmatcha/ui-clone-skills/ci.yml?branch=main&amp;label=CI&amp;style=flat-square"></a>
  <a href="./LICENSE.txt"><img alt="License" src="https://img.shields.io/github/license/voidmatcha/ui-clone-skills?style=flat-square"></a>
</p>

<p align="center">
  <a href="README.md">🇺🇸 English</a> | <a href="README.ko.md">🇰🇷 한국어</a> | <a href="README.ja.md">🇯🇵 日本語</a> | <strong>🇨🇳 简体中文</strong>
</p>

<!-- README-CANONICAL-REVISION: sha256=94c5893d3844012801dbd4251fea7ac2b0d4018a4484dfbd6b2b975e75c08243; bytes=exact-README.md-UTF-8; translation-quality=not-attested -->

`ui-clone-skills` 将一个真实网站转化为有证据支撑的 React + Tailwind 实现。它会捕获浏览器实际渲染的页面，下载真实的 CSS 和资源，读取响应式样式与计算样式，从 JavaScript bundle 中还原动画参数，并在不同视口和交互状态下验证结果。

这是面向动态网页的运动取证工具。对于那些截图转代码模型会遗漏关键细节的页面，它尤其适用：GSAP 时间线、Framer Motion 弹簧动画、Webflow IX2 交互、Lenis 平滑滚动、Lottie 播放、悬停状态、滚动显现、粘性区块以及响应式过渡。

| 输入一个真实 URL | 流水线执行的工作 | 最终产物 |
| --- | --- | --- |
| **捕获** | 记录桌面端、平板端、移动端以及滚动、悬停、点击和过渡的证据 | 参考帧、视频、DOM 映射和区块映射 |
| **解码** | 提取样式表、计算值、资源、字体、bundle 和运动参数 | `transition-spec.json`、运行时证据、实测布局数据 |
| **重建** | 根据观测到的结构和数值构建，而不是凭空设计外观 | React/TSX、Tailwind、保留的 CSS、本地资源 |
| **验证** | 通过布局门禁、绝对误差（AE）、结构相似性（SSIM）和运动检查对比参考页面与实现 | 可复现的通过/失败证据和限定范围的修复 |

## 立即试用

安装插件，然后向你的编码智能体提供一个真实 URL、目标范围和输出目录：

```text
Clone the hero and pricing sections from https://example.com into React + Tailwind.
Preserve the responsive layout, scroll reveals, and hover motion. Output to ./out/.
```

从 `ui-reverse-engineering` 开始。它会检测已有运行，从最后一个已经验证的流水线状态继续，并根据证据路由到捕获、提取、生成、验证或差异诊断流程，不会丢弃仍可使用的证据。

## 它有何不同

截图转代码工具会根据一帧或多帧像素推测实现方式。`ui-clone-skills` 能检查这些像素背后的真实在线源代码，再测试重建页面的行为是否一致。

| 常见的视觉生成器 | `ui-clone-skills` |
| --- | --- |
| 根据截图近似还原布局 | 下载 CSS 并测量实际渲染的 DOM |
| 猜测缓动、时长和触发时机 | 从 CSS 和 JavaScript bundle 中提取数值 |
| 只重建可见的桌面端画面 | 捕获桌面端、平板端、移动端和不同滚动位置 |
| 将运动效果视为后期添加的润色 | 在实现之前生成一份共享的运动规范 |
| 页面能够构建或看起来像就停止 | 必须提供渲染、结构、资源和运动证据 |

目标不是做出一个看似合理的仿制品，而是得到一个可将可见资源、DOM 结构、响应式行为和运动效果与参考页面逐项对比的复刻实现。

## 与其他开源工具相比有何不同

开源网站重建工具使用的证据和最终产物各不相同。请根据你需要的结果选择：

| 项目 | 最适合的用途 | 与 `ui-clone-skills` 的边界 |
| --- | --- | --- |
| [Screenshot to Code](https://github.com/abi/screenshot-to-code) | 将截图、模型稿、Figma 设计或屏幕录制转换为 HTML、React 或 Vue | 从视觉输入生成代码；`ui-clone-skills` 从真实 URL 开始，检查 CSS、bundle、运行时状态和交互证据 |
| [AI Website Cloner Template](https://github.com/JCodesMore/ai-website-cloner-template) | 通过计算样式研究、交互扫描、真实资源和并行构建智能体创建 Next.js 复刻 | 在本组比较中最为接近；`ui-clone-skills` 还加入可复用的捕获、诊断与审计工作流、从 bundle 提取的运动规范、可恢复的门禁，以及确定性的视觉与运动检查 |
| [Open Lovable](https://github.com/firecrawl/open-lovable) | 使用聊天应用和 Firecrawl 将网站重建为 React 应用 | 侧重应用生成体验；`ui-clone-skills` 侧重智能体流水线中的取证产物和实测一致性 |
| [GoClone](https://github.com/goclone-dev/goclone) | 下载 HTML、CSS、JavaScript、图片和链接，生成可浏览的静态镜像 | 为离线浏览保留网站文件；`ui-clone-skills` 生成 React + Tailwind 实现，并测试响应式与交互行为 |

如果 JavaScript bundle 中隐藏的动画参数很重要，需要审计现有实现，或必须通过可复现门禁而非构建成功和人工抽查来证明完成，请选择 `ui-clone-skills`。

<a id="what-it-recovers"></a>

## 它能还原什么

- **真实视觉数值：** 字体排印、间距、颜色、边框、变换、断点、CSS 自定义属性和原始类名
- **响应式结构：** 随视口变化的布局、流式 `vw`/`rem` 行为、粘性定位、网格位置和移动端重排
- **运动参数：** GSAP 与 ScrollTrigger 时间线、Framer Motion 弹簧参数、anime.js 时序、Webflow IX2 交互、Lenis 与 Locomotive 滚动设置、CSS 关键帧以及 Web Animations API 状态
- **交互状态：** 滚动显现与滚动同步动画、悬停和点击过渡、预加载器、页面过渡、轮播、标签页、菜单和定时序列
- **媒体与场景：** 图片、字体、视频、Lottie、Rive、Spline、canvas 和 WebGL 引用，并在可用时记录播放或交互证据
- **混淆后的输出：** 当 Tailwind、CSS Modules、CSS-in-JS 或压缩后的 bundle 隐藏作者编写的数值时，提取计算样式

提取引擎会写入共享产物，尤其是 `transition-spec.json`，让实现和验证使用同一份观测契约，而不是各自独立猜测。

## 会真正判定失败的验证

构建成功、HTTP 200、页面标题一致或截图看起来可信，都不代表任务完成。流水线会根据页面特性，使用相应证据检查实际渲染结果：

- 布局健康度以及 DOM/区块结构
- 文本、字体、可见资源和响应式一致性
- 绝对误差（AE）、SSIM 和区块级视觉对比
- 滚动终点、显现触发、悬停、点击和过渡状态对比
- 综合验证中以 60 fps 逐帧对比运动效果
- 静态检查提取出的运动条目是否被实现钩子覆盖

快速迭代时可以使用 `quick` 或 `standard` 验证层级。默认的 `comprehensive` 层级会保留完整的浏览器和运动检查。

常规对比使用确定性脚本，而不是让模型判断每一张截图。视觉能力仅用于最终语义审查，以及仅凭指标无法解释差异时的限定范围诊断。

<a id="skills"></a>

## 技能

| 你的需求 | 使用 | 负责产出的结果 |
| --- | --- | --- |
| 重建真实网站或继续已有运行 | **`ui-reverse-engineering`** | 一条由证据驱动的网站到 React 流水线，贯穿捕获、提取、生成和验证 |
| 捕获参考行为 | **`ui-capture`** | 截图，以及滚动、悬停、点击、过渡和可选的实现证据 |
| 诊断复刻结果为何存在差异 | **`visual-debug`** | AE/SSIM、计算样式、结构和运动层面的发现，以及具体修复方案 |

默认从 `ui-reverse-engineering` 进入。仅需要新的参考证据时，直接调用 `ui-capture`。如果参考产物和实现产物都已存在，而任务是解释两者差异，则调用 `visual-debug`。

Claude Code 和 Codex 都会公开相同的三个技能。两个宿主适配器共享相同的脚本、门禁、产物和钩子行为。

## 适用场景

| 你的来源 | 最佳选择 |
| --- | --- |
| 包含真实 CSS、资源、响应式行为和运动效果的**真实 URL** | **`ui-clone-skills`** |
| **Figma 文件** | Builder.io、Anima、Plasmic 或其他 Figma 实现工具 |
| **只有截图** | screenshot-to-code 或 v0 等截图转代码工具 |
| **只有文字描述** | v0、Lovable 或 Bolt 等设计生成器 |
| 只需要**静态镜像**的真实 URL | `wget --mirror` 或 HTTrack |

请勿使用它创造全新设计、绕过访问控制，或在未经许可的情况下发布受保护的第三方设计。当页面可以在真实浏览器中访问，并且目标是学习、原型设计、内部工具开发，或重建你有权复刻的网站时，它的效果最佳。

## 安装

运行一次安装程序。它会为你的 `PATH` 中检测到的所有受支持宿主 CLI 注册插件：

```bash
tmp=$(mktemp) && curl -LsSf -o "$tmp" https://raw.githubusercontent.com/voidmatcha/ui-clone-skills/main/install.sh && bash "$tmp" && rm -f "$tmp"
```

使用 `--claude-only` 或 `--codex-only` 仅安装到一个宿主。Claude Code 会获得插件和生命周期钩子。Codex 会获得三个公开技能，并在 `ui-reverse-engineering` 首次于某个工作区运行时启用项目本地钩子。

请参阅[安装指南](./README_detail/install.md)，了解从 checkout 安装、手动配置依赖项、宿主专用选项以及仅安装技能的方式。

## 要求

**已测试平台：** macOS 14+（主要平台），以及原生或通过 WSL2 运行的 Ubuntu 22.04+。不支持原生 Windows。

| 依赖项 | 用途 |
| --- | --- |
| `agent-browser` | 浏览器捕获、提取和交互对比 |
| ImageMagick | AE 像素对比 |
| `dssim` | 结构视觉相似度 |
| `ffmpeg` | 视频捕获和帧提取 |
| `uv` + Python 3.11+ | 流水线状态、门禁、钩子和指标 |

## 流水线如何工作

1. 在桌面端、平板端、移动端和相关交互状态下**捕获参考页面**。
2. 将页面**提取**为 DOM、CSS、资源、字体、区块、bundle 和运行时证据。
3. 将运动效果**解码**为有源代码证据支撑、包含触发条件和实测参数的过渡规范。
4. 根据捕获的结构和数值**生成实现**；当自由发挥式重建会损失保真度时，保留源 CSS。
5. 通过结构、视觉、响应式和运动门禁**验证渲染结果**。
6. **根据实测差异迭代**，只有满足用户要求的完成契约，或报告真实阻碍时才停止。

在 checkout 中，可使用 `python -m ui_clone.pipeline live_url component_name session_name status --json` 或 `node bin/ui-clone pipeline live_url component_name session_name status --json` 查看状态。npm 发布目前暂停，因此除非 `ui-clone-cli` 已通过 npm link 指向此仓库，否则请优先使用 checkout 中的命令。

## 文档

三个路由技能保持精简，仅在流水线步骤需要时加载 51 份聚焦的子文档。请先阅读任务级页面，需要确切命令或门禁行为时再查看运维契约。

- [安装与宿主配置](./README_detail/install.md)
- [完整的逆向工程流水线](./README_detail/ui-reverse-engineering.md)
- [参考页面与过渡捕获](./README_detail/ui-capture.md)
- [视觉与运动调试](./README_detail/visual-debug.md)
- [流水线钩子、状态与门禁](./README_detail/pipeline.md)
- [面向智能体的 CLI 契约](./docs/agent-cli.md)
- [Token 与提示词缓存管理](./README_detail/token-management.md)
- [安全模型](./README_detail/security.md)
- [负责任使用](./README_detail/responsible-use.md)
- [常见问题与框架支持](./README_detail/faq.md)

## 范围

生成结果是面向生产环境的 React + Tailwind 代码，但这并不自动保证复刻的第三方网站已获许可或可以直接公开部署。动态或受保护的资源、身份验证、反机器人系统、随机化场景以及无法访问的源 bundle 都可能限制提取能力。流水线会记录这些缺口，而不会悄悄将它们视为已经匹配。

全部三个技能都包含遵循 [Agent Skills](https://agentskills.io/) 格式的评测夹具。发布历史请参阅 [CHANGELOG.md](./CHANGELOG.md)。

## 许可证

Apache-2.0。请参阅 [LICENSE.txt](./LICENSE.txt)。
