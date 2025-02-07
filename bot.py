
import os
import openai
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

# Load API keys from environment variables
SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN")
SLACK_APP_TOKEN = os.getenv("SLACK_APP_TOKEN")  # For Socket Mode
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
SLACK_CHANNEL_ID = os.getenv("SLACK_CHANNEL_ID")

# Initialize OpenAI
openai.api_key = OPENAI_API_KEY

# Initialize Slack app
app = App(token=SLACK_BOT_TOKEN)

# Memory storage for conversations
conversation_memory = {}

# Function to call ChatGPT
def get_gpt_response(thread_id, message):
    if thread_id not in conversation_memory:
        conversation_memory[thread_id] = []
    
    conversation_memory[thread_id].append({"role": "user", "content": message})

    response = openai.ChatCompletion.create(
        model="gpt-4",
        messages=conversation_memory[thread_id]
    )
    
    reply = response["choices"][0]["message"]["content"]
    conversation_memory[thread_id].append({"role": "assistant", "content": reply})
    
    return reply

# Handle messages in the specified channel
@app.event("app_mention")
def handle_mention(event, say):
    text = event["text"]
    channel = event["channel"]
    thread_ts = event.get("thread_ts") or event["ts"]

    if channel == SLACK_CHANNEL_ID:
        response = get_gpt_response(thread_ts, text)
        say(text=response, thread_ts=thread_ts)

# Start the bot
if __name__ == "__main__":
    handler = SocketModeHandler(app, SLACK_APP_TOKEN)
    handler.start()
