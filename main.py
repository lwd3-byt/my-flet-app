import flet as ft
import feedparser
from bs4 import BeautifulSoup
from time import mktime
from datetime import datetime
import sqlite3
import hashlib
import threading
import requests

# ================= 配置区域 =================
RSS_SOURCES = {
    "AP News": "https://yzcw.dpdns.org/xml/apnews/world.xml",
    "Barron's": "https://yzcw.dpdns.org/users/1/web_requests/155/Barron.xml",
    "BBC": "https://yzcw.dpdns.org/users/1/web_requests/524/BBC-World.xml",
    "Bloomberg": "https://yzcw.dpdns.org/users/1/web_requests/287/Bloomberg-Economics.xml",
    "FT": "https://yzcw.dpdns.org/users/1/web_requests/512/FT-World-hvc9.xml",
    "NYT": "https://yzcw.dpdns.org/users/1/web_requests/520/NYT-World.xml",
    "Reuters": "https://yzcw.dpdns.org/users/1/web_requests/466/R-World-ggf765.xml"
}

DB_NAME = 'rss_local_db.db'
# ============================================

# AI 翻译与总结核心函数
def ai_translate_and_summarize(title, content):
    DMXAPI_API_KEY = "sk-QyYPGrePM9zkESjV1dWpUAp04TyAxvJOctufvoVxnoZtbSSu" 
    
    prompt = f"""你是一个专业的新闻翻译与总结助手。
请根据以下新闻内容，首先提供一段简明扼要的中文总结概括，然后再提供全文的流畅中文翻译。

新闻标题：{title}
新闻内容：{content}

请严格按照以下格式输出：
【内容摘要】
（在此输出总结概括...）

【全文翻译】
（在此输出全文中文翻译...）"""

    payload = {
        "model": "doubao-seed-2-0-lite-260215",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.3
        # 🌟 已移除 max_tokens，代表不设限，让 AI 自动按模型最大上限输出直到完成
    }

    headers = {
        "Authorization": f"Bearer {DMXAPI_API_KEY}",
        "Content-Type": "application/json"
    }

    try:
        response = requests.post(
            "https://www.dmxapi.cn/v1/chat/completions",
            headers=headers,
            json=payload,
            timeout=60
        )
        response.raise_for_status()
        result = response.json()["choices"][0]["message"]["content"].strip()
        return result
    except requests.exceptions.RequestException as e:
        return f"❌ AI网络请求失败: {e}"
    except Exception as e:
        return f"❌ AI接口调用出错: {e}"

# ================= 数据库操作 =================
def get_db_connection():
    return sqlite3.connect(DB_NAME, check_same_thread=False)

def init_database():
    conn = get_db_connection()
    cursor = conn.cursor()
    for source_name in RSS_SOURCES.keys():
        table_suffix = source_name.lower().replace("'", "").replace(" ", "_")
        table_name = f"rss_{table_suffix}"
        
        # ✨【建表升级】新增了 ai_translation 字段，用来永久保存 AI 结果
        create_table_sql = f"""
            CREATE TABLE IF NOT EXISTS {table_name} (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                link TEXT NOT NULL,
                link_hash TEXT NOT NULL UNIQUE,
                published_time TEXT,
                content TEXT,
                ai_translation TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """
        cursor.execute(create_table_sql)
        
        # 💡 为了防止用户旧数据库已经存在，在此强制安全地尝试添加新列
        try:
            cursor.execute(f"ALTER TABLE {table_name} ADD COLUMN ai_translation TEXT;")
        except sqlite3.OperationalError:
            pass # 如果列已经存在，会报错，这里直接忽略即可

    conn.commit()
    cursor.close()
    conn.close()

def fetch_and_store_single(source_name, rss_url):
    feed = feedparser.parse(rss_url)
    if not feed.entries:
        return 0

    table_suffix = source_name.lower().replace("'", "").replace(" ", "_")
    table_name = f"rss_{table_suffix}"
    
    conn = get_db_connection()
    cursor = conn.cursor()
    new_count = 0

    for entry in feed.entries:
        title = entry.get('title', '【无标题】')
        link = entry.get('link', '')
        if not link: 
            continue
            
        link_hash = hashlib.md5(link.encode('utf-8')).hexdigest()
        
        published_time_str = None
        if 'published_parsed' in entry and entry.published_parsed:
            published_time_str = datetime.fromtimestamp(mktime(entry.published_parsed)).strftime('%Y-%m-%d %H:%M:%S')

        raw_content = entry.content[0].value if 'content' in entry else entry.get('description', '【无内容】')
        clean_content = BeautifulSoup(raw_content, "html.parser").get_text(separator="\n", strip=True)
        
        sql = f"INSERT OR IGNORE INTO {table_name} (title, link, link_hash, published_time, content) VALUES (?, ?, ?, ?, ?)"
        try:
            cursor.execute(sql, (title, link, link_hash, published_time_str, clean_content))
            if cursor.rowcount > 0:
                new_count += 1
        except Exception as e:
            print(f"本地入库出错 [{title}]: {e}")

    conn.commit()
    cursor.close()
    conn.close()
    return new_count

# ✨【查询升级】把 ai_translation 和 link_hash 一并查出来
def query_articles(source_name):
    table_suffix = source_name.lower().replace("'", "").replace(" ", "_")
    table_name = f"rss_{table_suffix}"
    
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(f"SELECT title, published_time, content, link, ai_translation, link_hash FROM {table_name} ORDER BY published_time DESC LIMIT 50")
    rows = cursor.fetchall()
    cursor.close()
    conn.close()
    return rows

# ✨【保存升级】保存 AI 翻译结果到对应的文章行
def save_ai_result_to_db(source_name, link_hash, translation_text):
    table_suffix = source_name.lower().replace("'", "").replace(" ", "_")
    table_name = f"rss_{table_suffix}"
    
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(f"UPDATE {table_name} SET ai_translation = ? WHERE link_hash = ?", (translation_text, link_hash))
    conn.commit()
    cursor.close()
    conn.close()


# ================= Flet UI 界面模块 =================
def main(page: ft.Page):
    page.title = "我的本地 RSS 阅读器"
    page.theme_mode = ft.ThemeMode.SYSTEM
    page.padding = 10
    
    app_state = {
        "current_source": list(RSS_SOURCES.keys())[0]
    }
    
    article_list_view = ft.ListView(expand=True, spacing=12, padding=5)
    source_title_text = ft.Text(f"当前源: {app_state['current_source']}", size=16, weight=ft.FontWeight.W_500, color=ft.Colors.BLUE_GREY_400)

    # 显示文章内页详情的弹窗函数
    def show_article_details(title, pub_time, content, link, cached_ai_text, link_hash):
        # 初始化 UI 组件
        ai_result_text = ft.Text("", visible=False, selectable=True, size=15, color=ft.Colors.GREEN_800, weight=ft.FontWeight.W_500)
        progress_ring = ft.Row([ft.ProgressRing(width=20, height=20, stroke_width=2), ft.Text("AI正在翻译与总结中...", size=12, color=ft.Colors.GREY_600)], visible=False)
        ai_divider = ft.Divider(visible=False, color=ft.Colors.GREEN_200)

        def close_dialog(e):
            dialog.open = False
            page.update()

        def open_browser(e):
            page.launch_url(link)

        # 核心逻辑：触发 AI 翻译或加载缓存
        def trigger_ai_translation(e):
            ai_action_btn.disabled = True
            progress_ring.visible = True
            page.update()

            def ai_task():
                res = ai_translate_and_summarize(title, content)
                
                # 只有当非错误返回时，才写入数据库，防止把“报错信息”存进去了
                if "❌" not in res:
                    save_ai_result_to_db(app_state["current_source"], link_hash, res)
                    ai_action_btn.visible = False 
                else:
                    ai_action_btn.disabled = False # 失败了允许用户重试
                
                ai_result_text.value = res
                ai_result_text.visible = True
                ai_divider.visible = True
                progress_ring.visible = False
                page.update()

            threading.Thread(target=ai_task, daemon=True).start()

        ai_action_btn = ft.TextButton("🤖 AI翻译与总结", on_click=trigger_ai_translation, icon_color=ft.Colors.GREEN)

        # ✨【智能判定】如果数据库里原本就已经有翻译了，直接贴上去，并隐藏 AI 按钮
        if cached_ai_text:
            ai_result_text.value = cached_ai_text
            ai_result_text.visible = True
            ai_divider.visible = True
            ai_action_btn.visible = False

        dialog = ft.AlertDialog(
            title=ft.Text(title, size=20, weight=ft.FontWeight.BOLD),
            content=ft.Container(
                content=ft.Column([
                    ft.Text(pub_time or "未知时间", size=12, color=ft.Colors.GREY_500),
                    ft.Divider(),
                    
                    # AI 展示区
                    progress_ring,
                    ai_result_text,
                    ai_divider,
                    
                    # 原始外文内容
                    ft.Text(content, selectable=True, size=15, color=ft.Colors.BLUE_GREY_900)
                ], tight=True, scroll=ft.ScrollMode.AUTO),
                width=450,
                height=550,
            ),
            actions=[
                ft.Row([
                    ai_action_btn,
                    ft.Row([
                        ft.TextButton("查看原文", on_click=open_browser),
                        ft.TextButton("关闭", on_click=close_dialog),
                    ])
                ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN, expand=True)
            ],
            actions_alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
        )
        
        if dialog not in page.overlay:
            page.overlay.append(dialog)
        dialog.open = True
        page.update()

    def refresh_ui_list():
        article_list_view.controls.clear()
        source_title_text.value = f"当前源: {app_state['current_source']}"
        
        articles = query_articles(app_state["current_source"])
        
        if not articles:
            article_list_view.controls.append(
                ft.Container(
                    content=ft.Text("本地暂无数据，请点击刷新抓取", size=16, color=ft.Colors.GREY_500),
                    alignment="center",
                    padding=40
                )
            )
        else:
            def create_clickable_card(title, pub_time, content, link, cached_ai_text, link_hash):
                # 如果该文章已经翻译过了，卡片标题后加一个小图标作为提示
                title_elements = [ft.Text(title, size=18, weight=ft.FontWeight.BOLD, color=ft.Colors.BLUE_ACCENT_400)]
                if cached_ai_text:
                    title_elements.append(ft.Icon(ft.Icons.TRANSLATE, color=ft.Colors.GREEN_400, size=16))

                return ft.Container(
                    content=ft.Column([
                        ft.Row(title_elements, wrap=True, spacing=5),
                        ft.Text(pub_time or "未知时间", size=12, color=ft.Colors.GREY_500),
                        ft.Divider(height=5, color=ft.Colors.TRANSPARENT),
                        ft.Text(content, size=14, max_lines=3, overflow=ft.TextOverflow.ELLIPSIS),
                    ], spacing=5),
                    padding=15,
                    border_radius=8,
                    border=ft.Border.all(1, ft.Colors.GREY_300), 
                    bgcolor="#f4f5f7",          
                    ink=True,                                    
                    on_click=lambda e: show_article_details(title, pub_time, content, link, cached_ai_text, link_hash)
                )

            for title, pub_time, content, link, cached_ai_text, link_hash in articles:
                article_list_view.controls.append(
                    create_clickable_card(title, pub_time, content, link, cached_ai_text, link_hash)
                )
        page.update()

    def on_single_sync_click(e):
        target_source = app_state["current_source"]
        target_url = RSS_SOURCES[target_source]
        
        single_sync_btn.disabled = True
        single_sync_btn.text = "正在刷新此源..."
        progress_bar.visible = True
        page.update()

        def single_sync_task():
            try:
                new_count = fetch_and_store_single(target_source, target_url)
                msg = f"【{target_source}】刷新成功！新增 {new_count} 条。"
            except Exception:
                msg = f"【{target_source}】刷新失败，请检查网络。"
            
            single_sync_btn.disabled = False
            single_sync_btn.text = "刷新当前源"
            progress_bar.visible = False
            
            page.snack_bar = ft.SnackBar(ft.Text(msg))
            page.snack_bar.open = True
            page.update()
            refresh_ui_list()

        threading.Thread(target=single_sync_task, daemon=True).start()

    def background_sync_all(on_complete):
        total_new = 0
        for name, url in RSS_SOURCES.items():
            try:
                total_new += fetch_and_store_single(name, url)
            except Exception:
                pass
        on_complete(total_new)

    def on_all_sync_click(e):
        all_sync_button.disabled = True
        all_sync_button.text = "全局同步中..."
        progress_bar.visible = True
        page.update()
        
        def all_sync_finished(new_count):
            all_sync_button.disabled = False
            all_sync_button.text = "刷新全部"
            progress_bar.visible = False
            
            page.snack_bar = ft.SnackBar(ft.Text(f"全量同步完成！累计新增 {new_count} 条。"))
            page.snack_bar.open = True
            page.update() 
            refresh_ui_list()

        threading.Thread(target=background_sync_all, args=(all_sync_finished,), daemon=True).start()

    def on_source_change(e):
        app_state["current_source"] = list(RSS_SOURCES.keys())[e.control.selected_index]
        refresh_ui_list()

    all_sync_button = ft.OutlinedButton("刷新全部", icon=ft.Icons.ALL_INBOX, on_click=on_all_sync_click)
    single_sync_btn = ft.FilledButton("刷新当前源", icon=ft.Icons.REFRESH, on_click=on_single_sync_click)
    progress_bar = ft.ProgressBar(visible=False, color=ft.Colors.BLUE_ACCENT)
    
    page.add(
        ft.Row([
            ft.Text("我的本地 RSS", size=24, weight=ft.FontWeight.BOLD),
            all_sync_button
        ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
        progress_bar,
        ft.Divider(height=10),
        
        ft.Row([
            source_title_text,
            single_sync_btn
        ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
        
        ft.Divider(height=5, color=ft.Colors.TRANSPARENT),
        article_list_view
    )

    page.navigation_bar = ft.NavigationBar(
        destinations=[
            ft.NavigationBarDestination(icon=ft.Icons.ARTICLE, label=name) for name in RSS_SOURCES.keys()
        ],
        on_change=on_source_change
    )

    refresh_ui_list()

if __name__ == "__main__":
    init_database()
    ft.run(main)