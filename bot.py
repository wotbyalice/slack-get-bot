import os
import time
import logging
import openai
import json
from serpapi import GoogleSearch
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# Validate environment variables
SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN")
SLACK_APP_TOKEN = os.getenv("SLACK_APP_TOKEN")
SLACK_CHANNEL_ID = os.getenv("SLACK_CHANNEL_ID")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
SERPAPI_API_KEY = os.getenv("SERPAPI_API_KEY")

if not all([SLACK_BOT_TOKEN, SLACK_APP_TOKEN, SLACK_CHANNEL_ID, OPENAI_API_KEY, SERPAPI_API_KEY]):
   logger.error("🚨 Missing required environment variables. Exiting.")
   exit(1)

# Initialize Slack app
app = App(token=SLACK_BOT_TOKEN)
openai.api_key = OPENAI_API_KEY

# Fetch Bot ID dynamically
def get_bot_id():
   try:
       auth_response = app.client.auth_test()
       return auth_response["user_id"]
   except Exception as e:
       logger.error(f"🚨 Failed to retrieve bot ID: {e}")
       exit(1)

SLACK_BOT_ID = get_bot_id()

# Store bot start time
BOT_START_TIME = time.time()

# Memory Storage
conversation_memory = {}  # Stores per-thread conversation history
search_results_memory = {}  # Stores search results for each thread
silenced_threads = set()  # Tracks silenced threads
active_threads = set()  # Tracks active threads
paused_threads = set()  # Tracks paused threads
last_activity = {}  # Tracks last activity time

# Token limits
MAX_TOKENS_PER_THREAD = 5000
MAX_MESSAGES_TO_KEEP = 30
INACTIVE_THREAD_EXPIRATION = 86400  # 24 hours in seconds

def is_recent_message(event_ts):
   """Check if the message was sent after the bot restarted."""
   return float(event_ts) > BOT_START_TIME

def fetch_thread_history(channel, thread_ts):
   """Retrieve and store full thread history for better context-aware responses."""
   try:
       result = app.client.conversations_replies(channel=channel, ts=thread_ts)
       messages = result.get("messages", [])
       if thread_ts not in conversation_memory:
           conversation_memory[thread_ts] = []
       for message in messages:
           if "text" in message:
               conversation_memory[thread_ts].append({"role": "user", "content": message["text"]})
       last_activity[thread_ts] = time.time()  # Update last activity time
       return messages
   except Exception as e:
       logger.error(f"Error fetching thread history: {e}")
       return []

def summarize_search_results(search_results):
   """Summarize search results using OpenAI."""
   try:
       summary_prompt = [
           {"role": "system", "content": "Summarize the following search results into key points."},
           {"role": "user", "content": search_results}
       ]
       response = openai.ChatCompletion.create(
           model="gpt-4-turbo",
           messages=summary_prompt
       )
       summary = response["choices"][0]["message"]["content"]
       return summary
   except Exception as e:
       logger.error(f"Error summarizing search results: {e}")
       return "❌ Error summarizing search results."

def search_online(query, thread_ts):
   """Perform a Google search using SerpAPI, store results, and generate a summary."""
   try:
       if not SERPAPI_API_KEY:
           logger.error("🚨 SERPAPI_API_KEY is missing!")
           return "❌ Search API key is missing."

       params = {"engine": "google", "q": query, "api_key": SERPAPI_API_KEY}
       logger.info(f"🔎 Performing online search for: {query}")
       search = GoogleSearch(params)
       results = search.get_dict()

       if "organic_results" not in results:
           logger.error(f"❌ No results found: {json.dumps(results, indent=2)}")
           return "❌ No search results found."

       search_results = [f"🔹 *{item['title']}* - <{item['link']}>" for item in results["organic_results"][:5]]
       formatted_results = "\n".join(search_results) if search_results else "⚠️ No relevant results found."

       # Generate a summary of the search results
       summary = summarize_search_results(formatted_results)

       # Store both search results and summary in memory
       search_results_memory[thread_ts] = {
           "results": formatted_results,
           "summary": summary
       }

       # Return combined response
       return f"🔎 *Google Search Results for:* `{query}`\n{formatted_results}\n\n📝 *Summary of Results:*\n{summary}"

   except Exception as e:
       logger.error(f"❌ Google search failed: {e}")
       return "❌ Error fetching search results."

def get_gpt_response(thread_id, user_input):
   """Process user input and generate a response using stored search results and thread memory."""
   if thread_id not in conversation_memory:
       conversation_memory[thread_id] = []

   context_messages = []
   if thread_id in search_results_memory:
       context_messages.append({"role": "system", "content": f"Previous search results:\n{search_results_memory[thread_id]['results']}"})
   for msg in conversation_memory[thread_id][-MAX_MESSAGES_TO_KEEP:]:
       context_messages.append(msg)
   context_messages.append({"role": "user", "content": user_input})

   try:
       response = openai.ChatCompletion.create(
           model="gpt-4-turbo",
           messages=context_messages
       )
       reply = response["choices"][0]["message"]["content"]
       conversation_memory[thread_id].append({"role": "assistant", "content": reply})
       return reply
   except openai.error.RateLimitError:
       return "⚠️ OpenAI API quota exceeded. Please try again later."
   except Exception as e:
       return f"❌ An error occurred: {e}"

@app.event("app_mention")
def handle_mention(event, say):
   """Handle app mention events."""
   text = event["text"].strip()
   thread_ts = event.get("thread_ts") or event["ts"]

   if not is_recent_message(event["ts"]):
       return

   if thread_ts not in active_threads:
       active_threads.add(thread_ts)

   fetch_thread_history(event["channel"], thread_ts)

   # Check if the bot is mentioned directly
   if text.strip() == f"<@{SLACK_BOT_ID}>":
       if thread_ts in silenced_threads:
           silenced_threads.remove(thread_ts)
           say(text="🔄 Bot is now active again!", thread_ts=thread_ts)
           logger.info(f"🤖 Bot reactivated in thread: {thread_ts}")
       else:
           say(text="👋 Hello! How can I help you today?", thread_ts=thread_ts)
       return

   # Check if the bot should be silenced
   if "@killbot" in text.lower():
       silenced_threads.add(thread_ts)
       say(text="🔇 Bot silenced for this thread. Tag me again to wake me up.", thread_ts=thread_ts)
       logger.info(f"🤖 Bot silenced in thread: {thread_ts}")
       return

   # Do not respond if the thread is silenced
   if thread_ts in silenced_threads:
       return

   if text.lower().startswith("search:"):
       query = text.replace("search:", "").strip()
       search_response = search_online(query, thread_ts)
       say(text=search_response, thread_ts=thread_ts)
       return

   response = get_gpt_response(thread_ts, text)
   say(text=response, thread_ts=thread_ts)

@app.event("message")
def handle_message(event, say):
   """Handle messages inside active threads."""
   text = event.get("text", "").strip()
   thread_ts = event.get("thread_ts") or event["ts"]

   if not is_recent_message(event["ts"]):
       return

   # Do not respond if the thread is silenced
   if thread_ts in silenced_threads or thread_ts in paused_threads or thread_ts not in active_threads:
       return

   if "@killbot" in text.lower():
       silenced_threads.add(thread_ts)
       say(text="🔇 Bot silenced for this thread.", thread_ts=thread_ts)
       return

   if text.lower().startswith("search:"):
       query = text.replace("search:", "").strip()
       search_response = search_online(query, thread_ts)
       say(text=search_response, thread_ts=thread_ts)
       return

   response = get_gpt_response(thread_ts, text)
   say(text=response, thread_ts=thread_ts)

if __name__ == "__main__":
   logger.info("✅ Slack bot is running!")
   handler = SocketModeHandler(app, SLACK_APP_TOKEN)
   handler.start()