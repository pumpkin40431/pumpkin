import gradio as gr
import anthropic
import requests
import json
import os
import time
from datetime import datetime

# ============================================================
# 設定
# ============================================================
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
APIFY_API_TOKEN = os.environ.get("APIFY_API_TOKEN", "")
ACTOR_ID = "futurizerush/meta-threads-scraper-zh-tw"
HISTORY_FILE = "post_history.json"

# ============================================================
# 資料儲存
# ============================================================
def load_history():
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except:
            return []
    return []

def save_history(posts):
    existing = load_history()
    existing.extend(posts)
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(existing[-100:], f, ensure_ascii=False, indent=2)

# ============================================================
# Apify 爬取
# ============================================================
def scrape_threads(keywords: str, search_filter: str, max_posts: int):
    if not APIFY_API_TOKEN:
        return None, "❌ 未設定 APIFY_API_TOKEN"

    keyword_list = [k.strip() for k in keywords.split(",") if k.strip()]
    if not keyword_list:
        return None, "❌ 請輸入關鍵字"

    # 啟動 Actor
    run_url = f"https://api.apify.com/v2/acts/{ACTOR_ID}/runs"
    
    payload = {
        "queries": keyword_list,
        "searchType": search_filter,
        "maxPostsPerQuery": max_posts,
        "scrapePostReplies": False
    }
    
    headers = {
        "Content-Type": "application/json"
    }
    
    params = {
        "token": APIFY_API_TOKEN
    }

    try:
        resp = requests.post(run_url, json=payload, headers=headers, params=params, timeout=30)
        
        if resp.status_code != 201:
            return None, f"❌ 啟動失敗：HTTP {resp.status_code}\n{resp.text[:500]}"
        
        run_data = resp.json()
        run_id = run_data.get("data", {}).get("id")
        
        if not run_id:
            return None, f"❌ 無法取得 Run ID\n{resp.text[:300]}"

    except Exception as e:
        return None, f"❌ 啟動 Actor 失敗：{str(e)}"

    # 輪詢等待完成
    status_url = f"https://api.apify.com/v2/actor-runs/{run_id}"
    
    for attempt in range(60):  # 最多等 5 分鐘
        time.sleep(5)
        try:
            status_resp = requests.get(status_url, params=params, timeout=15)
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
        result_resp = requests.get(result_url, params={**params, "limit": max_posts}, timeout=30)
        items = result_resp.json()
        
        if not items:
            return None, "❌ 爬取結果為空，請換關鍵字試試"
        
        return items, f"✅ 成功爬取 {len(items)} 篇貼文"
        
    except Exception as e:
        return None, f"❌ 取得結果失敗：{str(e)}"

# ============================================================
# 計算互動分數
# ============================================================
def calc_engagement(post):
    likes = post.get("likes", post.get("likeCount", 0)) or 0
    replies = post.get("replies", post.get("replyCount", 0)) or 0
    reposts = post.get("reposts", post.get("repostCount", 0)) or 0
    quotes = post.get("quotes", post.get("quoteCount", 0)) or 0
    shares = post.get("shares", post.get("shareCount", 0)) or 0
    return likes * 1 + replies * 3 + reposts * 5 + quotes * 4 + shares * 4

# ============================================================
# Claude 分析
# ============================================================
def analyze_posts(posts: list):
    if not ANTHROPIC_API_KEY:
        return "❌ 未設定 ANTHROPIC_API_KEY"
    
    # 取前 5 名高互動貼文
    sorted_posts = sorted(posts, key=calc_engagement, reverse=True)[:5]
    
    post_texts = []
    for i, p in enumerate(sorted_posts, 1):
        text = p.get("text", p.get("content", p.get("caption", "")))
        likes = p.get("likes", p.get("likeCount", 0)) or 0
        replies = p.get("replies", p.get("replyCount", 0)) or 0
        score = calc_engagement(p)
        post_texts.append(f"【貼文{i}】互動分數:{score} 讚:{likes} 留言:{replies}\n{text}")
    
    combined = "\n\n---\n\n".join(post_texts)
    
    prompt = f"""你是社群媒體病毒式傳播專家。分析以下 Threads 高互動貼文，找出共同的爆紅模式。

{combined}

請分析：
1. **開頭鉤子**：這些貼文如何在前兩行抓住注意力？
2. **內容結構**：段落安排、節奏、換行技巧
3. **情緒觸發**：引發什麼情緒（共鳴、好奇、驚訝）？
4. **CTA 技巧**：如何引導留言或分享？
5. **爆紅公式**：總結可複製的寫作模板

用繁體中文回答，條列清楚。"""

    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        message = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=1500,
            messages=[{"role": "user", "content": prompt}]
        )
        return message.content[0].text
    except Exception as e:
        return f"❌ Claude 分析失敗：{str(e)}"

# ============================================================
# Claude 生成貼文
# ============================================================
def generate_posts(analysis: str, brand_name: str, brand_desc: str, product: str, target: str):
    if not ANTHROPIC_API_KEY:
        return "❌ 未設定 ANTHROPIC_API_KEY"
    
    prompt = f"""你是台灣眼鏡店的社群媒體專家，擅長寫 Threads 爆文。

品牌資訊：
- 店名：{brand_name}
- 描述：{brand_desc}
- 主打商品：{product}
- 目標客群：{target}

病毒式貼文分析結果：
{analysis}

請根據以上分析，以「誠實老闆」人設，生成 3 篇不同類型的 Threads 貼文草稿：

【類型1：痛點共鳴型】
目標：讓目標客群看到第一句就有感

【類型2：故事敘事型】
目標：用真實經歷建立信任感

【類型3：知識乾貨型】
目標：提供眼鏡相關實用知識，展現專業

每篇要求：
- 長度 150-300 字
- 符合 Threads 風格（短段落、換行、口語）
- 結尾加上引導留言的 CTA
- 用繁體中文，台灣口語

請直接輸出 3 篇貼文，用「===」分隔。"""

    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        message = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=2000,
            messages=[{"role": "user", "content": prompt}]
        )
        return message.content[0].text
    except Exception as e:
        return f"❌ 貼文生成失敗：{str(e)}"

# ============================================================
# 格式化爬取結果顯示
# ============================================================
def format_scrape_results(posts: list):
    if not posts:
        return ""
    
    sorted_posts = sorted(posts, key=calc_engagement, reverse=True)[:10]
    lines = [f"📊 共爬取 {len(posts)} 篇，顯示前 10 名高互動貼文\n"]
    
    for i, p in enumerate(sorted_posts, 1):
        text = p.get("text", p.get("content", p.get("caption", "")))
        likes = p.get("likes", p.get("likeCount", 0)) or 0
        replies = p.get("replies", p.get("replyCount", 0)) or 0
        reposts = p.get("reposts", p.get("repostCount", 0)) or 0
        score = calc_engagement(p)
        username = p.get("username", p.get("authorName", p.get("ownerUsername", "unknown")))
        
        preview = text[:150] + "..." if len(text) > 150 else text
        lines.append(
            f"{'='*40}\n"
            f"#{i} @{username} | 分數:{score} | 讚:{likes} 留言:{replies} 轉:{reposts}\n"
            f"{preview}\n"
        )
    
    return "\n".join(lines)

# ============================================================
# Gradio UI
# ============================================================
def build_ui():
    with gr.Blocks(title="Viral Threads Post Engine", theme=gr.themes.Soft()) as app:
        
        gr.Markdown("# 🧵 Viral Threads Post Engine\n### 台灣眼鏡店爆文生成器")
        
        # 儲存爬取資料的狀態
        scraped_data = gr.State([])
        analysis_data = gr.State("")
        
        # ── Step 1：爬取設定 ──────────────────────────────
        with gr.Group():
            gr.Markdown("## Step 1：設定爬取條件")
            
            keywords_input = gr.Textbox(
                label="關鍵字（多個用逗號分隔）",
                placeholder="眼鏡, 配眼鏡, 近視, 眼鏡推薦",
                value="眼鏡, 配眼鏡"
            )
            
            with gr.Row():
                filter_input = gr.Radio(
                    label="搜尋類型",
                    choices=["top", "recent"],
                    value="top"
                )
                max_posts_input = gr.Slider(
                    label="爬取數量",
                    minimum=5,
                    maximum=50,
                    value=20,
                    step=5
                )
            
            scrape_btn = gr.Button("🔍 開始爬取", variant="primary", size="lg")
            scrape_status = gr.Textbox(label="爬取狀態", interactive=False, lines=2)
        
        # ── Step 2：爬取結果 ──────────────────────────────
        with gr.Group():
            gr.Markdown("## Step 2：爬取結果")
            scrape_results = gr.Textbox(
                label="高互動貼文列表",
                interactive=False,
                lines=15,
                max_lines=20
            )
            analyze_btn = gr.Button("📊 分析爆紅模式", variant="primary", size="lg")
        
        # ── Step 3：病毒分析 ──────────────────────────────
        with gr.Group():
            gr.Markdown("## Step 3：病毒式傳播分析")
            analysis_output = gr.Textbox(
                label="爆紅模式分析",
                interactive=False,
                lines=15,
                max_lines=25
            )
        
        # ── Step 4：品牌設定 ──────────────────────────────
        with gr.Group():
            gr.Markdown("## Step 4：品牌設定")
            
            with gr.Row():
                brand_name_input = gr.Textbox(
                    label="店名",
                    placeholder="例：小明眼鏡",
                    value="誠實眼鏡"
                )
                product_input = gr.Textbox(
                    label="主打商品",
                    placeholder="例：日本手工框、散光隱形眼鏡"
                )
            
            brand_desc_input = gr.Textbox(
                label="品牌描述",
                placeholder="例：台北大安區在地眼鏡店，堅持不賣貴就是好的理念",
                lines=2
            )
            target_input = gr.Textbox(
                label="目標客群",
                placeholder="例：20-35歲上班族，重視CP值，第一次配眼鏡的年輕人",
                lines=2
            )
            
            generate_btn = gr.Button("✍️ 生成貼文草稿", variant="primary", size="lg")
        
        # ── Step 5：生成結果 ──────────────────────────────
        with gr.Group():
            gr.Markdown("## Step 5：貼文草稿")
            generated_output = gr.Textbox(
                label="3 篇貼文草稿",
                interactive=True,
                lines=20,
                max_lines=30
            )
            save_btn = gr.Button("💾 儲存到歷史記錄", variant="secondary")
            save_status = gr.Textbox(label="儲存狀態", interactive=False, lines=1)
        
        # ── 歷史記錄 ──────────────────────────────────────
        with gr.Accordion("📚 歷史記錄", open=False):
            history_output = gr.Textbox(
                label="過去生成的貼文",
                interactive=False,
                lines=10
            )
            load_history_btn = gr.Button("🔄 載入歷史記錄")
        
        # ============================================================
        # 事件綁定
        # ============================================================
        
        def do_scrape(keywords, search_filter, max_posts):
            posts, status = scrape_threads(keywords, search_filter, int(max_posts))
            if posts:
                display = format_scrape_results(posts)
                return posts, status, display
            else:
                return [], status, ""
        
        scrape_btn.click(
            fn=do_scrape,
            inputs=[keywords_input, filter_input, max_posts_input],
            outputs=[scraped_data, scrape_status, scrape_results]
        )
        
        def do_analyze(posts):
            if not posts:
                return "❌ 請先完成爬取", ""
            result = analyze_posts(posts)
            return result, result
        
        analyze_btn.click(
            fn=do_analyze,
            inputs=[scraped_data],
            outputs=[analysis_output, analysis_data]
        )
        
        def do_generate(analysis, brand_name, brand_desc, product, target):
            if not analysis:
                return "❌ 請先完成分析"
            return generate_posts(analysis, brand_name, brand_desc, product, target)
        
        generate_btn.click(
            fn=do_generate,
            inputs=[analysis_data, brand_name_input, brand_desc_input, product_input, target_input],
            outputs=[generated_output]
        )
        
        def do_save(posts_text):
            if not posts_text:
                return "❌ 沒有內容可儲存"
            entry = {
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "content": posts_text
            }
            save_history([entry])
            return "✅ 已儲存！"
        
        save_btn.click(
            fn=do_save,
            inputs=[generated_output],
            outputs=[save_status]
        )
        
        def do_load_history():
            history = load_history()
            if not history:
                return "尚無歷史記錄"
            lines = []
            for item in reversed(history[-10:]):
                lines.append(f"[{item.get('timestamp', '')}]\n{item.get('content', '')}\n{'='*50}")
            return "\n\n".join(lines)
        
        load_history_btn.click(
            fn=do_load_history,
            outputs=[history_output]
        )
    
    return app

# ============================================================
# 啟動
# ============================================================
if __name__ == "__main__":
    app = build_ui()
    app.launch(
        server_name="0.0.0.0",
        server_port=int(os.environ.get("PORT", 7860)),
        share=False
    )
