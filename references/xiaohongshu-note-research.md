---
name: xiaohongshu-note-research
description: Research and analyze a single Xiaohongshu note from a shared URL, including title/body, images, engagement, comments, and product/value implications. Use when the user asks to “研究一下”, “看看有什么价值”, summarize, extract, or evaluate a Xiaohongshu note.
---

# Xiaohongshu Note Research

Use this workflow for a single Xiaohongshu note URL when the goal is analysis/research, not collection organization.

## Workflow

1. Try the browser URL first to see whether the page is accessible and whether login/IP risk blocks rendering.
   - If browser navigation returns a security/login/IP-risk page, do not stop.
   - Continue with server-rendered/mobile-page extraction below.

2. Fetch the note HTML directly with a mobile user-agent.

   ```python
   import requests
   headers = {
       "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
       "Accept-Language": "zh-CN,zh;q=0.9",
   }
   html = requests.get(url, headers=headers, timeout=20, allow_redirects=True).text
   ```

3. Parse server-rendered state instead of scraping visible DOM only.
   - Look for `<script>window.__SETUP_SERVER_STATE__=...</script>` first.
   - Useful path:

   ```python
   data = json.loads(match.group(1))
   page = data["LAUNCHER_SSR_STORE_PAGE_DATA"]
   note = page["noteData"]
   comments = page.get("commentData", {})
   other_notes = page.get("userOtherNotesData", [])
   ```

4. Extract the factual note payload.
   - `note.title`
   - `note.desc`
   - `note.tagList`
   - `note.interactInfo` (`likedCount`, `collectedCount`, `commentCount`, `shareCount`)
   - `note.user`
   - `note.imageList`
   - `commentData.comments` plus nested `subComments`
   - `userOtherNotesData` for author/context signals when relevant

5. Download note images for visual/OCR analysis when the post is image-heavy.
   - Convert `http://` image URLs to `https://`.
   - Use `Referer: https://www.xiaohongshu.com/` and a normal browser UA.
   - Save to a temp directory such as `/tmp/xhs_<slug>_imgs/1.jpg`.

6. Analyze each image with vision/OCR.
   - Ask specifically to extract all text and focus on the user’s topic of interest.
   - Do not rely on the first/cover image only; tutorial posts often spread key setup commands across later images.

7. Synthesize value, not just a summary.
   - Separate: what the post says, engagement signals, comment-derived needs, implementation/config details, competitive mentions, and recommended actions.
   - For product/value research, explicitly call out:
     - user pain points
     - feature positioning
     - onboarding/config friction
     - platform-specific demand
     - competitor comparisons
     - concrete next steps

## Pitfalls

- Xiaohongshu browser rendering may show “IP 存在风险 / 300012” even when direct mobile HTML fetch succeeds.
- Meta tags can be empty; do not conclude content is unavailable until checking `window.__SETUP_SERVER_STATE__`.
- The note body may be duplicated in multiple inline script states; prefer the parsed JSON state.
- Comments can contain the highest-value product signals; always inspect nested `subComments`.
- Engagement ratios matter: high 收藏 vs low 点赞 often means tutorial/config intent.
- Vision/OCR may over-interpret decorative images; cross-check with extracted text and later tutorial images.

## Response Style

- Answer in Chinese unless the user asks otherwise.
- Start with a clear conclusion.
- Include concise evidence: title, author, engagement numbers, notable comments, and exact commands/configs if present.
- End with actionable recommendations when the user asks “有什么价值”.
