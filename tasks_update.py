# Update for tasks.py

# Change the import statement in tasks.py from:
# from chat_server import ai_respond_sync
# to:
from chat_server import get_ai_response

# Change the function call in process_whatsapp_message from:
# ai_reply, detected_intent, handoff_triggered = ai_respond_sync(
# to:
ai_reply, detected_intent, handoff_triggered = get_ai_response(
    convo_id=convo_id,
    username=username,
    conversation_history=conversation_history,
    user_message=message_body,
    chat_id=chat_id,
    channel="whatsapp",
    language=language
)