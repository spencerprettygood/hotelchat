# Original import (line 92):
# from chat_server import ai_respond_sync
# Replace with:
from chat_server import get_ai_response

# Original function call (line 176):
# ai_reply, detected_intent, handoff_triggered = ai_respond_sync(
#     convo_id=convo_id,
#     username=username,
#     conversation_history=conversation_history,
#     user_message=message_body,
#     chat_id=chat_id,
#     channel="whatsapp",
#     language=language
# )
# Replace with:
ai_reply, detected_intent, handoff_triggered = get_ai_response(
    convo_id=convo_id,
    username=username,
    conversation_history=conversation_history,
    user_message=message_body,
    chat_id=chat_id,
    channel="whatsapp",
    language=language
)