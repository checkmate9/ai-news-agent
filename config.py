import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env from the same directory as this file (works regardless of where script is run from)
load_dotenv(Path(__file__).parent / ".env", override=True)

# --- RSS Feeds ---
RSS_FEEDS = [
    # AI Labs
    {"url": "https://openai.com/blog/rss.xml",                          "source": "OpenAI Blog"},
    {"url": "https://www.anthropic.com/rss.xml",                        "source": "Anthropic Blog"},
    {"url": "https://deepmind.google/discover/blog/rss.xml",            "source": "Google DeepMind"},
    {"url": "https://huggingface.co/blog/feed.xml",                     "source": "HuggingFace Blog"},
    {"url": "https://mistral.ai/news/rss",                              "source": "Mistral AI"},
    {"url": "https://blogs.microsoft.com/ai/feed/",                     "source": "Microsoft AI"},
    # Newsletters
    {"url": "https://www.deeplearning.ai/the-batch/feed/",              "source": "The Batch (Andrew Ng)"},
    {"url": "https://jack-clark.net/feed/",                             "source": "Import AI"},
    {"url": "https://aibreakfast.beehiiv.com/feed",                     "source": "AI Breakfast"},
    # Tech Media
    {"url": "https://www.technologyreview.com/feed/",                   "source": "MIT Tech Review"},
    {"url": "https://techcrunch.com/category/artificial-intelligence/feed/", "source": "TechCrunch AI"},
    {"url": "https://www.theverge.com/rss/index.xml",                           "source": "The Verge"},
    # Research
    {"url": "https://arxiv.org/rss/cs.AI",                              "source": "ArXiv cs.AI"},
    {"url": "https://arxiv.org/rss/cs.LG",                              "source": "ArXiv cs.LG"},
    {"url": "https://paperswithcode.com/latest/research/rss",           "source": "Papers With Code"},
    # Substack newsletters
    {"url": "https://www.aitidbits.ai/feed",                            "source": "AI Tidbits (Substack)"},
    {"url": "https://thesequence.substack.com/feed",                    "source": "TheSequence (Substack)"},
    {"url": "https://lastweekin.ai/feed",                               "source": "Last Week in AI (Substack)"},
    {"url": "https://addai.substack.com/feed",                          "source": "Add AI (Substack)"},
]

# --- Reddit (PRAW - official API) ---
REDDIT_CLIENT_ID     = os.getenv("REDDIT_CLIENT_ID", "")
REDDIT_CLIENT_SECRET = os.getenv("REDDIT_CLIENT_SECRET", "")
REDDIT_USERNAME      = os.getenv("REDDIT_USERNAME", "")
REDDIT_PASSWORD      = os.getenv("REDDIT_PASSWORD", "")
REDDIT_ENABLED       = all([REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET, REDDIT_USERNAME, REDDIT_PASSWORD])

REDDIT_SUBREDDITS = [
    "MachineLearning",
    "LocalLLaMA",
    "artificial",
    "singularity",
    "ChatGPT",
]
REDDIT_MIN_SCORE = 50       # skip low-engagement posts
REDDIT_MAX_POSTS = 10       # per subreddit

# --- Hacker News ---
HN_MIN_SCORE = 20           # skip low-scored stories
HN_MAX_STORIES = 20         # total HN stories to include
HN_SEARCH_QUERY = "AI OR LLM OR GPT OR Claude OR machine learning OR neural"

# --- General ---
LOOKBACK_HOURS = 24         # only include items published in the last N hours
MAX_ITEMS_PER_SOURCE = 5    # cap per source to avoid one feed dominating

# --- Claude model ---
CLAUDE_MODEL = "claude-haiku-4-5-20251001"
MAX_TOKENS_SUMMARY = 4096

# --- Schedule ---
SCHEDULE_TIMES = os.getenv("SCHEDULE_TIMES", "08:00,18:00")
TIMEZONE = os.getenv("TIMEZONE", "America/New_York")

# --- Telegram ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# --- Prompts ---
SUMMARIZATION_SYSTEM_PROMPT = (
    "You are an AI research analyst curating a twice-daily digest for a technically "
    "sophisticated audience. You are concise, accurate, and highlight genuine novelty. "
    "You skip hype and focus on substance."
)

SUMMARIZATION_USER_PROMPT_TEMPLATE = """\
Below are AI news items collected from RSS feeds, Reddit, and Hacker News \
in the last {hours} hours. Sources include research blogs, tech media, and community discussions.

<items>
{items_block}
</items>

Your task:
1. Identify the most significant AI developments, announcements, or insights (aim for 10-15, but write about whatever is actually provided — even if there is only 1 item).
2. For each item write: a *bold headline*, a 2-3 sentence summary, and the source name.
3. Group items loosely by theme (models/research, industry/products, policy/safety, tools/open-source).
4. End with a *Trending themes* bullet list (max 5 bullets).
5. Skip duplicates, obvious reposts, and low-substance items.
6. Use plain text with minimal markdown — Telegram renders *bold* and _italic_.
7. On the very last line output exactly this (no label, just the pipe-separated URLs):
   Pick the 2 most impactful stories overall + 1 story from Geektime (Hebrew source) if present in the list, otherwise pick a 3rd impactful story.
TOP_LINKS: <url1> | <url2> | <url3>

Respond ONLY with the digest followed by the TOP_LINKS line. No preamble.\
"""
