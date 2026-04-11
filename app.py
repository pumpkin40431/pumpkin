import gradio as gr
import anthropic
import os
import json
import datetime
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# ===== 初始化 =====
def get_claude_client():
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return None
    return anthropic.Anthropic(api_key=api_key)

# ===== 歷史記錄 =====
history_db = []

def save_to_history(post_type, content, platform="threads"):
    entry = {
        "id": len(history_db) + 1,
        "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "type": post_type,
        "platform": platform,
        "content": content,
        "likes_estimate": 0
    }
    history_db.append(entry)
    return entry

# ===== 發送通知 =====
def send_email_notification(content, post_type):
    try:
        email = os.environ.get("GMAIL_ADDRESS")
        password = os.environ.get("GMAIL_APP_PASSWORD")
        notify = os.environ.get("NOTIFY_EMAIL")
        
        if not all([email, password, notify]):
            return False
            
        msg = MIMEMultipart()
        msg['From'] = email
        msg['To'] = notify
        msg['Subject'] = f"🔥 新文案已生成：{post_type}"
        
        body = f"""
新的 Threads 文案已生成！

類型：{post_type}
時間：{datetime.datetime.now().strftime("%Y-%m-%d %H:%M")}

內容：
{content}

---
Viral Thread Engine 自動通知
        """
        msg.attach(MIMEText(body, 'plain', 'utf-8'))
        
        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls()
        server.login(email, password)
        server.sendmail(email, notify, msg.as_string())
        server.quit()
        return True
    except:
        return False

# ===== 生成文案 =====
def generate_post(style, topic, extra_info):
    client = get_claude_client()
    if not client:
        return "❌ 找不到 Claude API Key，請確認 Secrets 設定"
    
    style_prompts = {
        "反直覺型": """
你是一個賣眼鏡的老闆，說話直接、真實、有點反骨。
寫一篇「反直覺」風格的 Threads 文案。
格式：
- 第一行：讓人意外的觀點（跟大眾想法相反）
- 中間：3-4行解釋為什麼
- 結尾：一個行動呼籲或反問

字數：150-250字
風格：像在跟朋友說話，不像廣告
""",
        "情感共鳴型": """
你是一個賣眼鏡的老闆，了解顧客的煩惱和心情。
寫一篇「情感共鳴」風格的 Threads 文案。
格式：
- 第一行：說出目標客群的痛點或心聲
- 中間：描述這個感受，讓人覺得「對！就是這樣！」
- 結尾：提供解決方向或共鳴結語

字數：150-250字
風格：溫暖、真實、不說教
""",
        "知識乾貨型": """
你是一個賣眼鏡的老闆，想分享真正有用的知識。
寫一篇「知識乾貨」風格的 Threads 文案。
格式：
- 第一行：一個讓人想繼續看的知識點
- 中間：3-5個實用資訊或數據
- 結尾：總結或呼籲

字數：150-250字
風格：專業但易懂，像在分享秘密
"""
    }
    
    prompt = f"""
{style_prompts.get(style, style_prompts["反直覺型"])}

主題：{topic}
額外資訊：{extra_info if extra_info else "無"}

請直接給我文案內容，不要加任何說明或前言。
文案要針對 20-40 歲、在意外表和生活品質的台灣人。
"""
    
    try:
        message = client.messages.create(
            model="claude-3-5-sonnet-20241022",
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}]
        )
        
        content = message.content[0].text
        save_to_history(style, content)
        send_email_notification(content, style)
        
        return content
    except Exception as e:
        return f"❌ 生成失敗：{str(e)}"

# ===== 爬取熱門文案 =====
def scrape_viral_posts(platform, keyword):
    try:
        apify_token = os.environ.get("APIFY_API_TOKEN")
        if not apify_token:
            return "❌ 找不到 Apify Token，請確認 Secrets 設定"
        
        from apify_client import ApifyClient
        client = ApifyClient(apify_token)
        
        results = []
        
        if platform == "TikTok":
            run_input = {
                "keywords": [keyword],
                "maxItems": 10,
                "dateRange": "LAST_7_DAYS"
            }
            run = client.actor("clockworks/free-tiktok-scraper").call(run_input=run_input)
            
            for item in client.dataset(run["defaultDatasetId"]).iterate_items():
                if item.get("diggCount", 0) > 500:
                    results.append({
                        "平台": "TikTok",
                        "內容": item.get("text", "")[:200],
                        "讚數": item.get("diggCount", 0),
                        "時間": item.get("createTime", "")
                    })
        
        elif platform == "Threads":
            run_input = {
                "queries": [keyword],
                "maxItems": 10
            }
            run = client.actor("apidojo/threads-scraper").call(run_input=run_input)
            
            for item in client.dataset(run["defaultDatasetId"]).iterate_items():
                like_count = item.get("likeCount", 0)
                if like_count > 500:
                    results.append({
                        "平台": "Threads",
                        "內容": item.get("text", "")[:200],
                        "讚數": like_count,
                        "時間": item.get("takenAt", "")
                    })
        
        if not results:
            return f"沒有找到超過500讚的 {keyword} 相關貼文\n\n試試換個關鍵字，例如：穿搭、外表、眼鏡"
        
        output = f"🔥 找到 {len(results)} 篇熱門貼文\n\n"
        for i, r in enumerate(results, 1):
            output += f"【第{i}篇】讚數：{r['讚數']}\n"
            output += f"{r['內容']}\n"
            output += "─" * 30 + "\n\n"
        
        return output
        
    except Exception as e:
        return f"❌ 爬取失敗：{str(e)}"

# ===== 分析文案結構 =====
def analyze_post(post_content):
    client = get_claude_client()
    if not client:
        return "❌ 找不到 Claude API Key"
    
    if not post_content.strip():
        return "請貼上要分析的文案"
    
    prompt = f"""
分析以下 Threads 文案的結構和為什麼有效：

文案內容：
{post_content}

請分析：
1. 開頭鉤子：用什麼方式吸引人繼續看？
2. 內容結構：怎麼組織資訊的？
3. 情緒觸發：觸動了什麼情緒或需求？
4. 行動呼籲：如何引導互動？
5. 可複製元素：哪些技巧可以用在眼鏡店文案？

用繁體中文回答，條列式，簡潔有力。
"""
    
    try:
        message = client.messages.create(
            model="claude-3-5-sonnet-20241022",
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}]
        )
        return message.content[0].text
    except Exception as e:
        return f"❌ 分析失敗：{str(e)}"

# ===== 顯示歷史記錄 =====
def show_history():
    if not history_db:
        return "還沒有生成過文案"
    
    output = f"📚 共生成 {len(history_db)} 篇文案\n\n"
    for entry in reversed(history_db[-10:]):
        output += f"【#{entry['id']}】{entry['timestamp']} | {entry['type']}\n"
        output += f"{entry['content'][:100]}...\n"
        output += "─" * 30 + "\n\n"
    
    return output

# ===== 建立介面 =====
with gr.Blocks(title="Viral Threads Post Engine") as app:
    
    gr.Markdown("# 🔥 Viral Threads Post Engine")
    gr.Markdown("眼鏡店專用｜20-40歲客群｜誠實老闆人設")
    
    with gr.Tabs():
        
        # Tab 1: 快速生成
        with gr.Tab("✍️ 快速生成"):
            gr.Markdown("### 選擇風格，輸入主題，一鍵生成")
            
            with gr.Row():
                with gr.Column():
                    style_choice = gr.Radio(
                        choices=["反直覺型", "情感共鳴型", "知識乾貨型"],
                        value="反直覺型",
                        label="文案風格"
                    )
                    topic_input = gr.Textbox(
                        label="主題",
                        placeholder="例如：為什麼便宜眼鏡反而貴、選錯鏡框的代價...",
                        lines=2
                    )
                    extra_input = gr.Textbox(
                        label="額外資訊（選填）",
                        placeholder="例如：最近有新款、季節換季、特定活動...",
                        lines=2
                    )
                    generate_btn = gr.Button("🚀 生成文案", variant="primary", size="lg")
                
                with gr.Column():
                    output_text = gr.Textbox(
                        label="生成結果",
                        lines=15
                    )
            
            generate_btn.click(
                fn=generate_post,
                inputs=[style_choice, topic_input, extra_input],
                outputs=output_text
            )
        
        # Tab 2: 爬取熱門
        with gr.Tab("🔍 爬取熱門文案"):
            gr.Markdown("### 找出現在正在爆紅的文案")
            
            with gr.Row():
                with gr.Column():
                    platform_choice = gr.Radio(
                        choices=["Threads", "TikTok"],
                        value="Threads",
                        label="平台"
                    )
                    keyword_input = gr.Textbox(
                        label="關鍵字",
                        placeholder="例如：眼鏡、穿搭、外表、配件",
                        value="眼鏡"
                    )
                    scrape_btn = gr.Button("🔍 開始爬取", variant="primary")
                
                with gr.Column():
                    scrape_output = gr.Textbox(
                        label="爬取結果",
                        lines=20
                    )
            
            scrape_btn.click(
                fn=scrape_viral_posts,
                inputs=[platform_choice, keyword_input],
                outputs=scrape_output
            )
        
        # Tab 3: 分析文案
        with gr.Tab("🧠 分析文案結構"):
            gr.Markdown("### 貼入任何爆文，分析它為什麼有效")
            
            with gr.Row():
                with gr.Column():
                    analyze_input = gr.Textbox(
                        label="貼入文案",
                        placeholder="把你找到的爆文貼進來...",
                        lines=10
                    )
                    analyze_btn = gr.Button("🧠 開始分析", variant="primary")
                
                with gr.Column():
                    analyze_output = gr.Textbox(
                        label="分析結果",
                        lines=15
                    )
            
            analyze_btn.click(
                fn=analyze_post,
                inputs=analyze_input,
                outputs=analyze_output
            )
        
        # Tab 4: 歷史記錄
        with gr.Tab("📚 歷史記錄"):
            gr.Markdown("### 所有生成過的文案")
            
            refresh_btn = gr.Button("🔄 更新記錄")
            history_output = gr.Textbox(
                label="歷史記錄",
                lines=20
            )
            
            refresh_btn.click(
                fn=show_history,
                outputs=history_output
            )

# ===== 啟動 =====
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 7860))
    app.launch(
        server_name="0.0.0.0",
        server_port=port,
        theme=gr.themes.Soft()
    )

