<h1 align="center">UI Clone Skills</h1>

<p align="center">
  <strong>웹사이트의 겉모습뿐 아니라 움직임까지 복제합니다.</strong>
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
  <a href="README.md">🇺🇸 English</a> | <strong>🇰🇷 한국어</strong> | <a href="README.ja.md">🇯🇵 日本語</a> | <a href="README.zh-cn.md">🇨🇳 简体中文</a>
</p>

<!-- README-CANONICAL-REVISION: sha256=94c5893d3844012801dbd4251fea7ac2b0d4018a4484dfbd6b2b975e75c08243; bytes=exact-README.md-UTF-8; translation-quality=not-attested -->

`ui-clone-skills`는 실제 웹사이트를 근거 기반의 React + Tailwind 구현으로 바꿉니다. 렌더링된 페이지를 캡처하고, 실제 CSS와 에셋을 내려받으며, 반응형 스타일과 계산된 스타일을 읽고, JavaScript 번들에서 애니메이션 매개변수를 복원한 뒤 여러 뷰포트와 상호작용 상태에서 결과를 검증합니다.

움직이는 웹을 위한 모션 포렌식 도구입니다. 스크린샷 기반 코드 생성 모델이 핵심을 놓치기 쉬운 페이지를 위해 만들었습니다. GSAP 타임라인, Framer Motion 스프링, Webflow IX2 인터랙션, Lenis 부드러운 스크롤, Lottie 재생, 호버 상태, 스크롤 리빌, 스티키 섹션, 반응형 전환까지 다룹니다.

| 하나의 실제 URL을 입력하면 | 파이프라인이 하는 일 | 결과물 |
| --- | --- | --- |
| **캡처** | 데스크톱, 태블릿, 모바일, 스크롤, 호버, 클릭, 전환 근거를 기록합니다 | 참조 프레임, 동영상, DOM 및 섹션 맵 |
| **디코딩** | 스타일시트, 계산값, 에셋, 폰트, 번들, 모션 매개변수를 추출합니다 | `transition-spec.json`, 런타임 근거, 측정된 레이아웃 데이터 |
| **재현** | 임의로 모양을 만들어 내지 않고 관찰한 구조와 값으로 구현합니다 | React/TSX, Tailwind, 보존된 CSS, 로컬 에셋 |
| **검증** | 레이아웃 게이트, 절대 오차(AE), 구조적 유사도(SSIM), 모션 검사로 참조와 구현을 비교합니다 | 재현 가능한 통과/실패 근거와 범위가 명확한 수정 사항 |

## 사용해 보기

플러그인을 설치한 다음, 코딩 에이전트에 실제 URL과 대상, 출력 디렉터리를 알려 주세요.

```text
Clone the hero and pricing sections from https://example.com into React + Tailwind.
Preserve the responsive layout, scroll reveals, and hover motion. Output to ./out/.
```

먼저 `ui-reverse-engineering`을 사용하세요. 기존 실행을 감지하고 마지막으로 입증된 파이프라인 상태부터 재개합니다. 이미 확보한 근거는 유지한 채 캡처, 추출, 생성, 검증 또는 불일치 진단 중 필요한 작업을 실행합니다.

## 무엇이 다른가요

스크린샷 기반 코드 생성 도구는 하나 이상의 프레임에 담긴 픽셀을 보고 구현을 추론합니다. `ui-clone-skills`는 그 픽셀을 만들어 낸 실제 원본을 조사하고, 재현한 페이지가 같은 방식으로 동작하는지 검사할 수 있습니다.

| 일반적인 시각적 코드 생성 도구 | `ui-clone-skills` |
| --- | --- |
| 스크린샷을 바탕으로 레이아웃을 근사합니다 | CSS를 내려받고 렌더링된 DOM을 측정합니다 |
| 이징, 지속 시간, 트리거 타이밍을 추측합니다 | CSS와 JavaScript 번들에서 값을 추출합니다 |
| 눈에 보이는 데스크톱 프레임만 재현합니다 | 데스크톱, 태블릿, 모바일, 여러 스크롤 위치를 캡처합니다 |
| 모션을 나중에 더하는 마감 요소로 취급합니다 | 구현 전에 공통 모션 명세를 만듭니다 |
| 빌드가 되거나 그럴듯해 보이면 멈춥니다 | 렌더링, 구조, 에셋, 모션 근거를 모두 요구합니다 |

목표는 그럴듯한 모방이 아닙니다. 눈에 보이는 에셋, DOM 구조, 반응형 동작, 모션을 참조 대상과 비교할 수 있는 복제본을 만드는 것이 목표입니다.

## 다른 오픈소스 도구와 무엇이 다른가요

오픈소스 웹사이트 재현 도구는 서로 다른 근거에서 시작하고 서로 다른 결과물에서 멈춥니다. 필요한 결과에 맞춰 선택하세요.

| 프로젝트 | 가장 잘 맞는 용도 | `ui-clone-skills`와의 차이 |
| --- | --- | --- |
| [Screenshot to Code](https://github.com/abi/screenshot-to-code) | 스크린샷, 목업, Figma 디자인, 화면 녹화를 HTML, React 또는 Vue로 변환 | 시각 자료에서 코드를 생성합니다. `ui-clone-skills`는 실제 URL에서 시작해 CSS, 번들, 런타임 상태, 상호작용 근거를 조사합니다 |
| [AI Website Cloner Template](https://github.com/JCodesMore/ai-website-cloner-template) | 계산된 스타일 조사, 상호작용 탐색, 실제 에셋, 병렬 빌더 에이전트를 이용해 Next.js 클론 제작 | 이 비교군에서 가장 비슷합니다. `ui-clone-skills`는 재사용 가능한 캡처·진단·점검 워크플로, 번들에서 추출한 모션 명세, 재개 가능한 게이트, 결정론적 시각·모션 검사를 추가합니다 |
| [Open Lovable](https://github.com/firecrawl/open-lovable) | 채팅 애플리케이션과 Firecrawl을 이용해 웹사이트를 React 앱으로 재현 | 앱 생성 경험에 초점을 둡니다. `ui-clone-skills`는 에이전트 파이프라인의 포렌식 산출물과 측정된 일치도에 초점을 둡니다 |
| [GoClone](https://github.com/goclone-dev/goclone) | HTML, CSS, JavaScript, 이미지, 링크를 내려받아 탐색 가능한 정적 미러 생성 | 오프라인 탐색용 사이트 파일을 보존합니다. `ui-clone-skills`는 React + Tailwind 구현을 만들고 반응형·상호작용 동작을 검사합니다 |

JavaScript 번들에 숨은 애니메이션 매개변수가 중요하거나, 기존 구현을 점검해야 하거나, 빌드와 육안 확인이 아니라 재현 가능한 게이트로 완료를 입증해야 한다면 `ui-clone-skills`를 선택하세요.

<a id="what-it-recovers"></a>

## 복원할 수 있는 것

- **실제 시각 값:** 타이포그래피, 간격, 색상, 테두리, 변환, 브레이크포인트, CSS 사용자 정의 속성, 원본 클래스 이름
- **반응형 구조:** 뷰포트별 레이아웃, 유동적인 `vw`/`rem` 동작, 스티키 배치, 그리드 배치, 모바일 리플로우
- **모션 매개변수:** GSAP 및 ScrollTrigger 타임라인, Framer Motion 스프링, anime.js 타이밍, Webflow IX2 인터랙션, Lenis 및 Locomotive 스크롤 설정, CSS 키프레임, Web Animations API 상태
- **상호작용 상태:** 스크롤 리빌 및 스크럽, 호버 및 클릭 전환, 프리로더, 페이지 전환, 슬라이더, 탭, 메뉴, 시간 기반 시퀀스
- **미디어와 장면:** 이미지, 폰트, 동영상, Lottie, Rive, Spline, 캔버스, WebGL 참조와 확인할 수 있는 재생·상호작용 근거
- **난독화된 출력:** Tailwind, CSS Modules, CSS-in-JS 또는 미니파이된 번들 때문에 작성값이 가려진 경우 계산된 스타일 추출

추출 엔진은 특히 `transition-spec.json` 같은 공통 아티팩트를 기록합니다. 덕분에 구현과 검증이 제각기 추측하지 않고 동일한 관측 명세를 기준으로 삼습니다.

## 실패를 제대로 잡아내는 검증

빌드 성공, HTTP 200, 일치하는 페이지 제목, 그럴듯한 스크린샷만으로는 완료가 아닙니다. 파이프라인은 페이지 특성에 맞는 근거로 렌더링 결과를 검사합니다.

- 레이아웃 건전성과 DOM/섹션 구조
- 텍스트, 폰트, 눈에 보이는 에셋, 반응형 동작의 일치 여부
- 절대 오차(AE), SSIM, 섹션 단위 시각 비교
- 스크롤 끝, 리빌 트리거, 호버, 클릭, 전환 상태 비교
- 종합 검증에서는 60 fps로 프레임 단위 모션 비교
- 추출한 모션 항목과 구현 훅 사이의 정적 커버리지

빠르게 반복할 때는 `quick` 또는 `standard` 검증 티어를 사용할 수 있습니다. 기본값인 `comprehensive` 티어는 전체 브라우저 및 모션 검사를 유지합니다.

일상적인 비교에는 모델이 모든 스크린샷을 판단하게 하는 대신 결정론적 스크립트를 사용합니다. 비전은 최종 결과의 의미적 일치 여부를 검토하거나, 메트릭만으로 불일치 원인을 설명할 수 없어 범위를 좁혀 진단할 때만 사용합니다.

<a id="skills"></a>

## Skills

| 필요한 작업 | 사용할 스킬 | 담당 결과물 |
| --- | --- | --- |
| 실제 사이트를 재현하거나 기존 실행을 재개 | **`ui-reverse-engineering`** | 캡처, 추출, 생성, 검증을 거쳐 근거에 따라 웹사이트를 React로 구현하는 파이프라인 |
| 참조 동작 캡처 | **`ui-capture`** | 스크린샷과 스크롤, 호버, 클릭, 전환 근거, 필요할 경우 구현 측 근거 |
| 복제본이 다른 이유 진단 | **`visual-debug`** | AE/SSIM, 계산된 스타일, 구조, 모션 분석 결과와 구체적인 수정 사항 |

기본 진입점으로 `ui-reverse-engineering`을 사용하세요. 새로운 참조 근거만 필요하면 `ui-capture`를 직접 호출하고, 참조 및 구현 아티팩트가 이미 있으며 불일치 원인을 설명해야 한다면 `visual-debug`를 호출하세요.

Claude Code와 Codex는 동일한 세 가지 공개 스킬을 제공합니다. 두 호스트의 어댑터는 같은 스크립트, 게이트, 아티팩트, 훅 동작을 공유합니다.

## 언제 사용하나요

| 원본 | 가장 적합한 도구 |
| --- | --- |
| 실제 CSS, 에셋, 반응형 동작, 모션이 있는 **실제 URL** | **`ui-clone-skills`** |
| **Figma 파일** | Builder.io, Anima, Plasmic 또는 다른 Figma 구현 도구 |
| **스크린샷만 있는 경우** | screenshot-to-code 또는 v0 같은 스크린샷 기반 코드 생성 도구 |
| **텍스트 설명만 있는 경우** | v0, Lovable 또는 Bolt 같은 디자인 생성 도구 |
| **정적 미러**만 필요한 실제 URL | `wget --mirror` 또는 HTTrack |

새 디자인을 만들거나, 접근 제어를 우회하거나, 제3자가 권리를 보유한 디자인을 허가 없이 공개하는 용도로 사용하지 마세요. 실제 브라우저에서 페이지에 접근할 수 있고 학습, 프로토타이핑, 내부 도구 제작 또는 복제 권한이 있는 사이트의 재구축이 목적일 때 가장 효과적입니다.

## 설치

설치 프로그램을 한 번 실행하세요. `PATH`에서 발견한 지원 대상 호스트 CLI마다 플러그인을 등록합니다.

```bash
tmp=$(mktemp) && curl -LsSf -o "$tmp" https://raw.githubusercontent.com/voidmatcha/ui-clone-skills/main/install.sh && bash "$tmp" && rm -f "$tmp"
```

호스트 하나만 대상으로 삼으려면 `--claude-only` 또는 `--codex-only`를 사용하세요. Claude Code에는 플러그인과 라이프사이클 훅을 설치합니다. Codex에는 공개 스킬 세 가지를 설치하고, 워크스페이스에서 `ui-reverse-engineering`을 처음 실행할 때 프로젝트 로컬 훅을 활성화합니다.

체크아웃 설치, 수동 의존성 설정, 호스트별 플래그, 스킬만 설치하는 방법은 [설치 가이드](./README_detail/install.md)를 참고하세요.

## 요구 사항

**테스트 환경:** macOS 14+ (주 환경), Ubuntu 22.04+ (네이티브 또는 WSL2). Windows 네이티브 환경은 지원하지 않습니다.

| 의존성 | 용도 |
| --- | --- |
| `agent-browser` | 브라우저 캡처, 추출, 상호작용 비교 |
| ImageMagick | AE 픽셀 비교 |
| `dssim` | 구조적 시각 유사도 |
| `ffmpeg` | 동영상 캡처 및 프레임 추출 |
| `uv` + Python 3.11+ | 파이프라인 상태, 게이트, 훅, 메트릭 |

## 파이프라인 작동 방식

1. 데스크톱, 태블릿, 모바일 및 관련 상호작용 상태에서 **참조 대상을 캡처합니다.**
2. 페이지를 DOM, CSS, 에셋, 폰트, 섹션, 번들, 런타임 근거로 **추출합니다.**
3. 출처에서 확인한 트리거와 측정값을 전환 명세에 기록해 **모션을 디코딩합니다.**
4. 캡처한 구조와 값으로 **구현을 생성하며**, 직접 재구현할 때 충실도가 떨어진다면 원본 CSS를 보존합니다.
5. 구조, 시각, 반응형, 모션 게이트로 **렌더링 결과를 검증합니다.**
6. **측정된 불일치를 반복해서 수정하고**, 요청한 완료 계약을 충족하거나 실제 장애 요인을 보고할 때만 멈춥니다.

체크아웃에서는 `python -m ui_clone.pipeline live_url component_name session_name status --json` 또는 `node bin/ui-clone pipeline live_url component_name session_name status --json`으로 상태를 확인하세요. npm 배포는 중단된 상태이므로 `ui-clone-cli`가 이 저장소에 npm 링크되어 있지 않다면 체크아웃 내부 명령을 사용하세요.

## 문서

세 가지 스킬은 간결하게 유지하며, 각 파이프라인 단계에서 필요할 때만 세부 문서 51개를 불러옵니다. 먼저 작업별 문서로 시작하고, 정확한 명령이나 게이트 동작이 필요할 때 운영 규약을 확인하세요.

- [설치 및 호스트 설정](./README_detail/install.md)
- [전체 리버스 엔지니어링 파이프라인](./README_detail/ui-reverse-engineering.md)
- [참조 및 전환 캡처](./README_detail/ui-capture.md)
- [시각 및 모션 디버깅](./README_detail/visual-debug.md)
- [파이프라인 훅, 상태, 게이트](./README_detail/pipeline.md)
- [에이전트용 CLI 계약](./docs/agent-cli.md)
- [토큰 및 프롬프트 캐시 관리](./README_detail/token-management.md)
- [보안 모델](./README_detail/security.md)
- [책임 있는 사용](./README_detail/responsible-use.md)
- [FAQ 및 프레임워크 지원](./README_detail/faq.md)

## 범위

생성 결과는 프로덕션 환경을 고려한 React + Tailwind 코드입니다. 그러나 복제한 타사 사이트에 적법한 라이선스가 있거나 공개 배포 준비가 끝났음을 자동으로 보장하지는 않습니다. 동적 또는 보호된 에셋, 인증, 봇 방지 시스템, 무작위 장면, 접근할 수 없는 원본 번들은 추출을 제한할 수 있습니다. 파이프라인은 이런 간극을 말없이 일치한 것으로 간주하지 않고 기록합니다.

세 가지 스킬 모두 [Agent Skills](https://agentskills.io/) 형식의 평가 픽스처를 포함합니다. 릴리스 내역은 [CHANGELOG.md](./CHANGELOG.md)를 참고하세요.

## 라이선스

Apache-2.0. [LICENSE.txt](./LICENSE.txt)를 참고하세요.
