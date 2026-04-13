import gradio as gr
import anthropic
import requests
import json
import os
import time
from datetime import datetime

# ==================== 設定 ====================
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
APIFY_API_TOKEN = os.environ.get("APIFY_API_TOKEN")
ACTOR_ID = "burton~threads-search-scraper"
HISTORY_FILE = "post_history.json"

# ==================== 歷史記錄 ====================
def load_history():
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return []

def save_history(record):
    history = load_history()
    history.insert(0, record)
    history = history[:100]
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)

# ==================== Apify 爬蟲 ====================
def run_apify_actor(search_query, sort, max_posts):
    """單一關鍵字執行 Actor"""
    url = f"https://api.apify.com/v2/acts/{ACTOR_ID}/runs"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {APIFY_API_TOKEN}"
    }
    payload = {
        "searchQuery": str(search_query).strip(),
        "sort": sort,
        "maxPosts": int(max_posts)
    }

    # 啟動 Actor
    response = requests.post(url, headers=headers, json=payload)
    if response.status_code not in [200, 201]:
        raise Exception(f"啟動失敗：{response.status_code} - {response.text}")

    run_data = response.json()
    run_id = run_data.get("data", {}).get("id")
    if not run_id:
        raise Exception(f"無法取得 Run ID：{run_data}")

    # 輪詢等待完成
    status_url = f"https://api.apify.com/v2/actor-runs/{run_id}"
    for attempt in range(60):
        time.sleep(5)
        status_resp = requests.get(status_url, headers={"Authorization": f"Bearer {APIFY_API_TOKEN}"})
        status_data = status_resp.json().get("data", {})
        status = status_data.get("status")

        if status == "SUCCEEDED":
            break
        elif status in ["FAILED", "ABORTED", "TIMED-OUT"]:
            raise Exception(f"Actor 執行失敗：{status}")

    # 取得結果
    dataset_id = status_data.get("defaultDatasetId")
    if not dataset_id:
        raise Exception("無法取得 Dataset ID")

    results_url = f"https://api.apify.com/v2/datasets/{dataset_id}/items"
    results_resp = requests.get(
        results_url,
        headers={"Authorization": f"Bearer {APIFY_API_TOKEN}"},
        params={"clean": True, "format": "json"}
    )
    return results_resp.json()

def scrape_threads(keywords_input, sort_type, max_posts):
    """多關鍵字爬取並合併結果"""
    try:
        # 解析關鍵字
        keywords = [k.strip() for k in keywords_input.split(",") if k.strip()]
        if not keywords:
            return [], "❌ 請輸入至少一個關鍵字"

        # sort_type UI 對應
        sort = "top" if sort_type == "熱門貼文" else "recent"

        all_posts = []
        seen_ids = set()
        errors = []

        for i, keyword in enumerate(keywords):
            try:
                posts = run_apify_actor(keyword, sort, int(max_posts))
                for post in posts:
                    post_id = post.get("id") or post.get("post_id") or post.get("url", "")
                    if post_id and post_id not in seen_ids:
                        seen_ids.add(post_id)
                        all_posts.append(post)
            except Exception as e:
                errors.append(f"關鍵字「{keyword}」失敗：{str(e)}")

        if not all_posts:
            error_msg = "\n".join(errors) if errors else "未抓取到任何貼文"
            return [], f"❌ {error_msg}"

        # 計算互動分數並排序
        for post in all_posts:
            likes = post.get("like_count", 0) or 0
            replies = post.get("reply_count", 0) or 0
            reposts = post.get("repost_count", 0) or 0
            quotes = post.get("quote_count", 0) or 0
            shares = post.get("share_count", 0) or 0
            views = post.get("view_count", 0) or 0
            post["engagement_score"] = (
                likes * 1 + replies * 3 + reposts * 5 + quotes * 4 + shares * 4
            )
            post["view_count"] = views

        # 依排序方式決定順序
        if sort_type == "最高瀏覽次數":
            all_posts.sort(key=lambda x: x.get("view_count", 0), reverse=True)
        else:
            all_posts.sort(key=lambda x: x.get("engagement_score", 0), reverse=True)

        status_msg = f"✅ 成功抓取 {len(all_posts)} 筆貼文"
        if errors:
            status_msg += f"（{len(errors)} 個關鍵字失敗）"

        return all_posts, status_msg

    except Exception as e:
        return [], f"❌ 錯誤：{str(e)}"

# ==================== 圖片偵測 ====================
def detect_has_image(post):
    image_fields = ["images", "media", "attachments", "carousel_media", "image_url", "media_url"]
    for field in image_fields:
        val = post.get(field)
        if val:
            if isinstance(val, list) and len(val) > 0:
                return True
            if isinstance(val, str) and val.strip():
                return True
    return False

# ==================== 顯示貼文 ====================
def format_posts_display(posts):
    if not posts:
        return "尚無資料"

    lines = []
    for i, post in enumerate(posts[:20], 1):
        # 取得內容
        content = (
            post.get("text_content") or
            post.get("content") or
            post.get("text") or
            post.get("caption") or
            "（無文字內容）"
        )

        # 取得作者
        username = post.get("username") or post.get("author") or "unknown"

        # 互動數據
        likes = post.get("like_count", 0) or 0
        replies = post.get("reply_count", 0) or 0
        reposts = post.get("repost_count", 0) or 0
        views = post.get("view_count", 0) or 0
        score = post.get("engagement_score", 0)

        # 圖片標記
        has_image = detect_has_image(post)
        media_tag = "📷 含圖片" if has_image else "📝 純文字"

        # 連結
        post_url = (
            post.get("post_url") or
            post.get("permalink") or
            post.get("url") or ""
        )
        if not post_url:
            post_id = post.get("id") or post.get("post_id") or ""
            if username and post_id:
                post_url = f"https://www.threads.net/@{username}/post/{post_id}"

        link_md = f"[🔗 查看原文]({post_url})" if post_url else "（無連結）"

        lines.append(
            f"### {i}. @{username} {media_tag}\n"
            f"{content[:200]}{'...' if len(content) > 200 else ''}\n\n"
            f"❤️ {likes} | 💬 {replies} | 🔁 {reposts} | 👀 {views} | 🏆 {score}\n\n"
            f"{link_md}\n\n"
            f"---"
        )

    return "\n\n".join(lines)

# ==================== Claude 分析 ====================
def analyze_viral_posts(posts):
    if not posts:
        return "❌ 請先抓取貼文"

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    top_posts = posts[:10]
    posts_text = ""
    for i, post in enumerate(top_posts, 1):
        content = (
            post.get("text_content") or
            post.get("content") or
            post.get("text") or ""
        )
        has_image = detect_has_image(post)
        media_type = "含圖片" if has_image else "純文字"
        posts_text += (
            f"貼文{i} [{media_type}]：\n{content}\n"
            f"互動分數：{post.get('engagement_score', 0)}\n"
            f"按讚：{post.get('like_count', 0)} | "
            f"留言：{post.get('reply_count', 0)} | "
            f"轉發：{post.get('repost_count', 0)}\n\n"
        )

    prompt = f"""分析以下 Threads 熱門貼文，找出病毒式傳播的規律：

{posts_text}

請分析：
1. **開頭鉤子**：這些貼文如何在前兩行抓住注意力？
2. **內容結構**：段落安排、節奏、長短有什麼規律？
3. **情緒觸發**：引發什麼情緒讓人想互動或分享？
4. **行動呼籲**：結尾如何促進留言或轉發？
5. **圖文策略**：哪些用了圖片？圖片對互動率有什麼影響？

請用繁體中文回答，條列重點，每點2-3句話。"""

    message = client.messages.create(
        model="claude-haiku-4-5",
        max_tokens=1500,
        messages=[{"role": "user", "content": prompt}]
    )

    return message.content[0].text

# ==================== Claude 生成貼文 ====================
def generate_posts(posts, analysis, brand_name, product_focus, brand_voice, target_pain):
    if not posts:
        return "❌ 請先抓取貼文"
    if not analysis or analysis.startswith("❌"):
        return "❌ 請先完成病毒分析"

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    prompt = f"""你是一個台灣眼鏡店老闆，經營品牌「{brand_name}」。
主打商品：{product_focus}
品牌風格：{brand_voice}
目標客群痛點：{target_pain}

根據以下病毒式貼文分析，為 Threads 平台生成 3 篇貼文草稿：

【病毒分析結果】
{analysis}

【要求】
- 使用「誠實老闆」persona：真實、不誇大、有點自嘲、站在消費者角度說話
- 目標受眾：20-40歲台灣人
- 每篇開頭必須在前兩行抓住注意力
- 使用繁體中文、台灣用語
- 每篇 150-300 字
- 結尾要有互動呼籲（問問題或請分享）

請生成以下 3 種類型：

**草稿一：痛點共鳴型**
（從消費者痛點切入，讓人覺得「說到我心裡了」）

**草稿二：故事敘事型**
（用真實店主視角分享一個小故事或觀察）

**草稿三：教育乾貨型**
（分享實用知識，讓人想收藏或轉發）

每篇草稿後面附上：
- 建議配圖：（描述什麼樣的圖片效果最好）
- 最佳發文時間：（根據台灣 Threads 用戶習慣）"""

    message = client.messages.create(
        model="claude-haiku-4-5",
        max_tokens=2000,
        messages=[{"role": "user", "content": prompt}]
    )

    result = message.content[0].text

    # 儲存歷史
    save_history({
        "timestamp": datetime.now().isoformat(),
        "brand": brand_name,
        "product": product_focus,
        "generated_posts": result
    })

    return result

# ==================== Gradio UI ====================
def scrape_and_display(keywords, sort_type, max_posts):
    posts, status = scrape_threads(keywords, sort_type, int(max_posts))
    display = format_posts_display(posts)
    return posts, display, status

def run_analysis(posts):
    if not posts:
        return "❌ 請先抓取貼文"
    return analyze_viral_posts(posts)

def run_generation(posts, analysis, brand_name, product_focus, brand_voice, target_pain):
    return generate_posts(posts, analysis, brand_name, product_focus, brand_voice, target_pain)

def format_history():
    history = load_history()
    if not history:
        return "尚無歷史記錄"
    lines = []
    for record in history[:10]:
        lines.append(
            f"### {record.get('timestamp', '')[:16]} | {record.get('brand', '')} - {record.get('product', '')}\n"
            f"{record.get('generated_posts', '')[:300]}...\n\n---"
        )
    return "\n\n".join(lines)

# ==================== 建立 UI ====================
with gr.Blocks(title="Viral Threads Post Engine", theme=gr.themes.Soft()) as app:
    # 儲存爬取結果的狀態
    scraped_posts_state = gr.State([])

    gr.Markdown("# 🧵 Viral Threads Post Engine\n### 台灣眼鏡店專用 | 誠實老闆版")

    # ── Step 1：爬蟲設定 ──
    with gr.Group():
        gr.Markdown("## Step 1：設定爬蟲")
        keywords_input = gr.Textbox(
            label="搜尋關鍵字（多個用逗號分隔）",
            placeholder="例如：眼鏡, 配鏡, 近視",
            value="眼鏡, 配鏡"
        )
        with gr.Row():
            sort_type = gr.Radio(
                choices=["熱門貼文", "最高瀏覽次數"],
                value="熱門貼文",
                label="排序方式"
            )
            max_posts = gr.Slider(
                minimum=10,
                maximum=100,
                value=20,
                step=10,
                label="每個關鍵字最多抓取幾篇"
            )
        scrape_btn = gr.Button("🔍 開始抓取", variant="primary")
        scrape_status = gr.Textbox(label="狀態", interactive=False)

    # ── Step 2：爬蟲結果 ──
    with gr.Group():
        gr.Markdown("## Step 2：爬蟲結果")
        posts_display = gr.Markdown("尚未抓取資料")

    # ── Step 3：病毒分析 ──
    with gr.Group():
        gr.Markdown("## Step 3：病毒式傳播分析")
        analyze_btn = gr.Button("🔬 分析熱門貼文", variant="secondary")
        analysis_output = gr.Markdown("尚未分析")

    # ── Step 4：品牌設定 ──
    with gr.Group():
        gr.Markdown("## Step 4：品牌設定")
        with gr.Row():
            brand_name = gr.Textbox(
                label="品牌名稱",
                placeholder="例如：見山眼鏡",
                value="見山眼鏡"
            )
            product_focus = gr.Textbox(
                label="主打商品",
                placeholder="例如：日本手工框、抗藍光鏡片",
                value="日本手工框"
            )
        brand_voice = gr.Textbox(
            label="品牌風格",
            placeholder="例如：誠實、不推銷、像朋友建議",
            value="誠實、不推銷、像朋友建議"
        )
        target_pain = gr.Textbox(
            label="目標客群痛點",
            placeholder="例如：不知道如何選框、怕被推銷貴的",
            value="不知道如何選框、怕被推銷貴的"
        )

    # ── Step 5：生成貼文 ──
    with gr.Group():
        gr.Markdown("## Step 5：生成 Threads 貼文")
        generate_btn = gr.Button("✍️ 生成貼文草稿", variant="primary")
        generated_output = gr.Markdown("尚未生成")

    # ── 歷史記錄 ──
    with gr.Accordion("📚 歷史記錄", open=False):
        history_btn = gr.Button("載入歷史記錄")
        history_display = gr.Markdown()

    # ── 事件綁定 ──
    scrape_btn.click(
        fn=scrape_and_display,
        inputs=[keywords_input, sort_type, max_posts],
        outputs=[scraped_posts_state, posts_display, scrape_status]
    )

    analyze_btn.click(
        fn=run_analysis,
        inputs=[scraped_posts_state],
        outputs=[analysis_output]
    )

    generate_btn.click(
        fn=run_generation,
        inputs=[
            scraped_posts_state,
            analysis_output,
            brand_name,
            product_focus,
            brand_voice,
            target_pain
        ],
        outputs=[generated_output]
    )

    history_btn.click(
        fn=format_history,
        outputs=[history_display]
    )

if __name__ == "__main__":
    app.launch(server_name="0.0.0.0", server_port=int(os.environ.get("PORT", 7860)))
