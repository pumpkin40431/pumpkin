import gradio as gr
import anthropic
import requests
import json
import os
import time
from datetime import datetime

# ============================================================
# 環境變數
# ============================================================
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
APIFY_API_TOKEN = os.environ.get("APIFY_API_TOKEN", "")
ACTOR_ID = "futurizerush~meta-threads-scraper-zh-tw"
HISTORY_FILE = "post_history.json"

# ============================================================
# 歷史記錄
# ============================================================
def load_history():
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except:
            return []
    return []

def save_history(post: str, post_type: str, keywords: str):
    history = load_history()
    history.insert(0, {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "type": post_type,
        "keywords": keywords,
        "content": post
    })
    history = history[:100]
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)

def format_history():
    history = load_history()
    if not history:
        return "尚無歷史記錄"
    result = ""
    for i, item in enumerate(history[:20], 1):
        result += f"**{i}. [{item['timestamp']}] {item['type']}** — 關鍵字：{item['keywords']}\n\n"
        result += f"{item['content'][:200]}...\n\n---\n\n"
    return result

# ============================================================
# Apify 爬取
# ============================================================
def scrape_threads(keywords: str, search_filter: str, max_posts: int):
    if not APIFY_API_TOKEN:
        return None, "❌ 未設定 APIFY_API_TOKEN"

    keyword_list = [k.strip() for k in keywords.split(",") if k.strip()]
    if not keyword_list:
        return None, "❌ 請輸入關鍵字"

    run_url = f"https://api.apify.com/v2/acts/{ACTOR_ID}/runs"

    payload = {
        "mode": "search",
        "keywords": keyword_list,
        "search_filter": "top",
        "max_posts": int(max_posts),  # ✅ 強制整數，唯一正確欄位名稱
    }

    headers = {"Content-Type": "application/json"}
    params = {
        "token": APIFY_API_TOKEN,
        "maxItems": int(max_posts),   # ✅ Apify 平台層級限制
    }

    try:
        resp = requests.post(
            run_url,
            json=payload,
            headers=headers,
            params=params,
            timeout=30
        )

        if resp.status_code != 201:
            return None, f"❌ 啟動失敗：HTTP {resp.status_code}\n{resp.text[:500]}"

        run_data = resp.json()
        run_id = run_data.get("data", {}).get("id")

        if not run_id:
            return None, f"❌ 無法取得 Run ID\n{resp.text[:300]}"

    except Exception as e:
        return None, f"❌ 啟動 Actor 失敗：{str(e)}"

    # 輪詢等待完成（最多 5 分鐘）
    status_url = f"https://api.apify.com/v2/actor-runs/{run_id}"
    status_data = {}

    for attempt in range(60):
        time.sleep(5)
        try:
            status_resp = requests.get(
                status_url,
                params={"token": APIFY_API_TOKEN},
                timeout=15
            )
            status_data = status_resp.json()
            status = status_data.get("data", {}).get("status", "")

            if status == "SUCCEEDED":
                break
            elif status in ["FAILED", "ABORTED", "TIMED-OUT"]:
                return None, f"❌ Actor 執行失敗，狀態：{status}"

        except Exception as e:
            continue
    else:
        return None, "❌ 等待超時（5分鐘），請稍後再試"

    # 取得結果
    dataset_id = status_data.get("data", {}).get("defaultDatasetId")
    if not dataset_id:
        return None, "❌ 無法取得 Dataset ID"

    result_url = f"https://api.apify.com/v2/datasets/{dataset_id}/items"

    try:
        result_resp = requests.get(
            result_url,
            params={
                "token": APIFY_API_TOKEN,
                "limit": int(max_posts)
            },
            timeout=30
        )
        items = result_resp.json()

        if not items:
            return None, "❌ 爬取結果為空，請換關鍵字試試"

        return items, f"✅ 成功爬取 {len(items)} 篇貼文"

    except Exception as e:
        return None, f"❌ 取得結果失敗：{str(e)}"

# ============================================================
# 資料處理
# ============================================================
def detect_image(post: dict) -> str:
    image_fields = ["images", "media", "attachments", "carousel_media", "image_url"]
    for field in image_fields:
        val = post.get(field)
        if val:
            if isinstance(val, list) and len(val) > 0:
                return "📷 含圖片"
            if isinstance(val, str) and val.strip():
                return "📷 含圖片"
    return "📝 純文字"

def get_post_url(post: dict) -> str:
    if post.get("post_url"):
        return post["post_url"]
    if post.get("permalink"):
        return post["permalink"]
    username = post.get("username", "")
    post_id = post.get("post_id") or post.get("id", "")
    if username and post_id:
        return f"https://www.threads.net/@{username}/post/{post_id}"
    return ""

def calculate_engagement(post: dict) -> int:
    likes = int(post.get("like_count", 0) or 0)
    replies = int(post.get("reply_count", 0) or 0)
    reposts = int(post.get("repost_count", 0) or 0)
    quotes = int(post.get("quote_count", 0) or 0)
    shares = int(post.get("share_count", 0) or 0)
    return likes * 1 + replies * 3 + reposts * 5 + quotes * 4 + shares * 4

def sort_posts(posts: list, sort_type: str) -> list:
    if sort_type == "最高瀏覽次數":
        return sorted(posts, key=lambda x: int(x.get("view_count", 0) or 0), reverse=True)
    else:
        return sorted(posts, key=calculate_engagement, reverse=True)

def format_posts_display(posts: list, sort_type: str) -> str:
    sorted_posts = sort_posts(posts, sort_type)
    output = ""
    for i, post in enumerate(sorted_posts[:10], 1):
        text = post.get("text_content", "") or post.get("content", "") or post.get("text", "") or ""
        username = post.get("username", "未知用戶")
        likes = post.get("like_count", 0) or 0
        replies = post.get("reply_count", 0) or 0
        reposts = post.get("repost_count", 0) or 0
        views = post.get("view_count", 0) or 0
        engagement = calculate_engagement(post)
        image_tag = detect_image(post)
        url = get_post_url(post)

        link_text = f"[查看原文]({url})" if url else "（無連結）"

        output += f"### {i}. @{username} {image_tag}\n"
        output += f"{text[:200]}{'...' if len(text) > 200 else ''}\n\n"
        output += f"❤️ {likes} 　💬 {replies} 　🔁 {reposts} 　👁️ {views} 　📊 互動分：{engagement}\n\n"
        output += f"{link_text}\n\n---\n\n"

    return output

# ============================================================
# Claude 分析
# ============================================================
def analyze_posts(posts: list, sort_type: str) -> str:
    if not ANTHROPIC_API_KEY:
        return "❌ 未設定 ANTHROPIC_API_KEY"

    sorted_posts = sort_posts(posts, sort_type)
    top_posts = sorted_posts[:5]

    posts_text = ""
    for i, post in enumerate(top_posts, 1):
        text = post.get("text_content", "") or post.get("content", "") or post.get("text", "") or ""
        engagement = calculate_engagement(post)
        image_tag = detect_image(post)
        posts_text += f"{i}. {image_tag} 互動分{engagement}\n{text[:300]}\n\n"

    prompt = f"""你是社群媒體病毒式傳播分析師，專門分析 Threads 爆紅貼文。

以下是 5 篇高互動 Threads 貼文：

{posts_text}

請分析這些貼文的共同規律，從以下 5 個角度提供具體洞察：

1. **開場鉤子**：前幾個字如何抓住注意力？用了什麼手法？
2. **內容結構**：如何組織資訊讓人讀完？
3. **情感觸發**：引發了哪些情緒反應？
4. **行動召喚**：如何引導留言、分享、收藏？
5. **圖文策略**：含圖片vs純文字的表現差異？哪種更有效？

請用繁體中文回答，每點給出 2-3 個具體例子或建議。"""

    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        message = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=1500,
            messages=[{"role": "user", "content": prompt}]
        )
        return message.content[0].text
    except Exception as e:
        return f"❌ 分析失敗：{str(e)}"

# ============================================================
# Claude 生成貼文
# ============================================================
def generate_posts(posts: list, sort_type: str, brand_name: str,
                   brand_desc: str, target_audience: str, brand_tone: str) -> tuple:
    if not ANTHROPIC_API_KEY:
        return "❌ 未設定 ANTHROPIC_API_KEY", "", ""

    sorted_posts = sort_posts(posts, sort_type)
    top_posts = sorted_posts[:5]

    posts_text = ""
    for i, post in enumerate(top_posts, 1):
        text = post.get("text_content", "") or post.get("content", "") or post.get("text", "") or ""
        engagement = calculate_engagement(post)
        image_tag = detect_image(post)
        posts_text += f"{i}. {image_tag} 互動分{engagement}\n{text[:300]}\n\n"

    prompt = f"""你是台灣 Threads 文案專家，擅長寫出高互動的貼文。

品牌資訊：
- 品牌名稱：{brand_name}
- 品牌描述：{brand_desc}
- 目標受眾：{target_audience}
- 品牌語調：{brand_tone}

參考的高互動貼文（學習其結構和手法，不要抄內容）：
{posts_text}

請以「誠實老闆」人設，為這個眼鏡品牌寫 3 種不同類型的 Threads 貼文草稿：

**類型一：痛點共鳴型**
- 開頭直擊目標受眾的日常痛點
- 引發「對！就是這樣！」的共鳴
- 自然帶出品牌解決方案
- 150字以內

**類型二：故事敘事型**
- 用真實客人或自身故事開場
- 有起伏、有轉折
- 讓人想看完、想留言
- 200字以內

**類型三：乾貨教育型**
- 提供真正有用的眼鏡/穿搭知識
- 用條列或數字讓人容易讀
- 讓人想收藏和分享
- 200字以內

每篇貼文請包含：
- 主文內容
- 建議的 hashtag（3-5個）
- 圖文建議（需要圖片嗎？拍什麼？）

請用繁體中文，語氣自然像真人在說話。"""

    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        message = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=2000,
            messages=[{"role": "user", "content": prompt}]
        )
        full_response = message.content[0].text

        # 分割三種類型
        parts = full_response.split("**類型")
        if len(parts) >= 4:
            post1 = "**類型" + parts[1]
            post2 = "**類型" + parts[2]
            post3 = "**類型" + parts[3]
        else:
            post1 = full_response
            post2 = "（請重新生成）"
            post3 = "（請重新生成）"

        # 儲存歷史
        keywords = "手動生成"
        save_history(post1, "痛點共鳴型", keywords)
        save_history(post2, "故事敘事型", keywords)
        save_history(post3, "乾貨教育型", keywords)

        return post1, post2, post3

    except Exception as e:
        return f"❌ 生成失敗：{str(e)}", "", ""

# ============================================================
# Gradio UI
# ============================================================
def do_scrape(keywords, sort_type, max_posts):
    posts, message = scrape_threads(keywords, sort_type, max_posts)
    if posts is None:
        return message, "", gr.update(visible=False), gr.update(visible=False), None

    display = format_posts_display(posts, sort_type)
    return message, display, gr.update(visible=True), gr.update(visible=True), posts

def do_analyze(posts, sort_type):
    if posts is None:
        return "❌ 請先爬取貼文"
    return analyze_posts(posts, sort_type)

def do_generate(posts, sort_type, brand_name, brand_desc, target_audience, brand_tone):
    if posts is None:
        return "❌ 請先爬取貼文", "", ""
    return generate_posts(posts, sort_type, brand_name, brand_desc, target_audience, brand_tone)

# ============================================================
# 建立 UI
# ============================================================
with gr.Blocks(title="Viral Threads Post Engine", theme=gr.themes.Soft()) as demo:

    # 狀態
    scraped_posts = gr.State(None)

    gr.Markdown("""
    # 🧵 Viral Threads Post Engine
    ### 台灣眼鏡店專用｜爬取爆紅貼文 → 分析規律 → 生成文案
    """)

    # ── Step 1：爬取設定 ──
    with gr.Group():
        gr.Markdown("## Step 1｜爬取設定")
        with gr.Row():
            keywords_input = gr.Textbox(
                label="搜尋關鍵字",
                placeholder="例如：眼鏡, 穿搭, 臉型",
                value="眼鏡,穿搭",
                scale=3
            )
            sort_type_input = gr.Radio(
                label="排序方式",
                choices=["熱門貼文", "最高瀏覽次數"],
                value="熱門貼文",
                scale=2
            )
            max_posts_input = gr.Slider(
                label="爬取數量",
                minimum=5,
                maximum=50,
                value=10,
                step=5,
                scale=1
            )
        scrape_btn = gr.Button("🔍 開始爬取", variant="primary", size="lg")

    # ── Step 2：爬取結果 ──
    with gr.Group():
        gr.Markdown("## Step 2｜爬取結果")
        scrape_status = gr.Textbox(label="爬取狀態", interactive=False)
        posts_display = gr.Markdown(label="貼文列表")
        analyze_btn = gr.Button("🧠 分析爆紅規律", variant="secondary", visible=False)

    # ── Step 3：病毒分析 ──
    with gr.Group():
        gr.Markdown("## Step 3｜病毒式傳播分析")
        analysis_output = gr.Markdown()
        proceed_btn = gr.Button("✍️ 進入文案生成", variant="secondary", visible=False)

    # ── Step 4：品牌設定 ──
    with gr.Group():
        gr.Markdown("## Step 4｜品牌設定")
        with gr.Row():
            brand_name_input = gr.Textbox(
                label="品牌名稱",
                value="見你眼鏡",
                scale=1
            )
            brand_desc_input = gr.Textbox(
                label="品牌描述",
                value="台灣獨立眼鏡店，專注幫客人找到最適合臉型的眼鏡",
                scale=3
            )
        with gr.Row():
            target_audience_input = gr.Textbox(
                label="目標受眾",
                value="20-40歲，注重穿搭品味，想找到適合自己的眼鏡",
                scale=2
            )
            brand_tone_input = gr.Textbox(
                label="品牌語調",
                value="誠實、專業、有溫度，像朋友給建議",
                scale=2
            )
        generate_btn = gr.Button("🚀 生成三款貼文草稿", variant="primary", size="lg")

    # ── Step 5：生成結果 ──
    with gr.Group():
        gr.Markdown("## Step 5｜貼文草稿")
        with gr.Row():
            post1_output = gr.Markdown(label="😤 痛點共鳴型")
            post2_output = gr.Markdown(label="📖 故事敘事型")
            post3_output = gr.Markdown(label="📚 乾貨教育型")

    # ── 歷史記錄 ──
    with gr.Accordion("📋 生成歷史", open=False):
        history_display = gr.Markdown()
        refresh_history_btn = gr.Button("🔄 重新整理歷史")

    # ── 事件綁定 ──
    scrape_btn.click(
        fn=do_scrape,
        inputs=[keywords_input, sort_type_input, max_posts_input],
        outputs=[scrape_status, posts_display, analyze_btn, proceed_btn, scraped_posts]
    )

    analyze_btn.click(
        fn=do_analyze,
        inputs=[scraped_posts, sort_type_input],
        outputs=[analysis_output]
    )

    proceed_btn.click(
        fn=lambda: gr.update(visible=True),
        outputs=[generate_btn]
    )

    generate_btn.click(
        fn=do_generate,
        inputs=[
            scraped_posts, sort_type_input,
            brand_name_input, brand_desc_input,
            target_audience_input, brand_tone_input
        ],
        outputs=[post1_output, post2_output, post3_output]
    )

    refresh_history_btn.click(
        fn=format_history,
        outputs=[history_display]
    )

# ============================================================
# 啟動
# ============================================================
if __name__ == "__main__":
    demo.launch(
        server_name="0.0.0.0",
        server_port=int(os.environ.get("PORT", 7860)),
        show_error=True
    )
