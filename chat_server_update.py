# Function to be added to chat_server.py to replace both ai_respond and ai_respond_sync

def get_ai_response(convo_id, username, conversation_history, user_message, chat_id, channel, language="en"):
    """
    Synchronous function to get AI responses using the OpenAI API.
    Replaces the previous asyncio-based implementation.
    """
    start_time_ai = time.time()
    logger.info(f"GET_AI_RESPONSE initiated for convo_id: {convo_id}, user: {username}, channel: {channel}, lang: {language}. Message: '{user_message[:50]}...'")
    
    # Ensure conversation_history is a list of dicts
    if not isinstance(conversation_history, list) or not all(isinstance(msg, dict) for msg in conversation_history):
        logger.warning(f"Invalid conversation_history format for convo_id {convo_id}. Resetting to current message only.")
        conversation_history = [{"role": "user", "content": user_message}]
    elif not conversation_history: # Ensure it's not empty
        conversation_history = [{"role": "user", "content": user_message}]
    
    # Add the current user message to the history if it's not already the last message
    if not conversation_history or conversation_history[-1].get("content") != user_message or conversation_history[-1].get("role") != "user":
        conversation_history.append({"role": "user", "content": user_message})
    
    # Limit history length to avoid excessive token usage (e.g., last 10 messages)
    MAX_HISTORY_LEN = 10
    if len(conversation_history) > MAX_HISTORY_LEN:
        conversation_history = conversation_history[-MAX_HISTORY_LEN:]
        logger.debug(f"Trimmed conversation history to last {MAX_HISTORY_LEN} messages for convo_id {convo_id}")
    
    system_prompt = f"You are a helpful assistant for Amapola Resort. Current language for response: {language}. Training document: {TRAINING_DOCUMENT[:200]}..." # Truncate for logging
    messages_for_openai = [
        {"role": "system", "content": system_prompt}
    ] + conversation_history
    
    ai_reply = None
    detected_intent = None # Placeholder for intent detection
    handoff_triggered = False # Placeholder for handoff logic
    
    request_start_time = time.time()
    logger.info(f"[OpenAI Request] Convo ID: {convo_id} - Model: gpt-4o-mini - User Message: '{user_message[:100]}...'") # Log request details
    logger.debug(f"[OpenAI Request Details] Convo ID: {convo_id} - Full History: {conversation_history}")
    
    try:
        logger.info(f"Calling OpenAI API for convo_id {convo_id}. Model: gpt-4o-mini. History length: {len(messages_for_openai)}")
        
        # Call OpenAI API synchronously
        response = openai_client.chat.completions.create(
            model="gpt-4o-mini", # Using the specified model
            messages=messages_for_openai,
            max_tokens=300, # Max tokens for the response
            temperature=0.7
        )
        
        ai_reply = response.choices[0].message.content.strip()
        usage = response.usage
        processing_time = (time.time() - request_start_time) * 1000
        logger.info(
            f"[OpenAI Response] Convo ID: {convo_id} - Reply: '{ai_reply[:100]}...' - Tokens: P{usage.prompt_tokens}/C{usage.completion_tokens}/T{usage.total_tokens} - Time: {processing_time:.2f}ms"
        )
        logger.debug(f"[OpenAI Response Details] Convo ID: {convo_id} - Full Reply: {ai_reply} - Full Response Object: {response.model_dump_json(indent=2)}")
        
        # Basic intent detection (example - can be expanded)
        if "book a room" in user_message.lower() or "reservation" in user_message.lower():
            detected_intent = "booking_inquiry"
        if "human" in user_message.lower() or "agent" in user_message.lower() or "speak to someone" in user_message.lower():
            handoff_triggered = True
            logger.info(f"Handoff to human agent triggered by user message for convo_id {convo_id}")
            
    except RateLimitError as e:
        logger.error(f"❌ OpenAI RateLimitError for convo_id {convo_id}: {str(e)}", exc_info=True)
        ai_reply = "I'm currently experiencing high demand. Please try again in a moment."
    except APITimeoutError as e:
        logger.error(f"❌ OpenAI APITimeoutError for convo_id {convo_id}: {str(e)}", exc_info=True)
        ai_reply = "I'm having trouble connecting to my brain right now. Please try again shortly."
    except APIError as e: # General API error
        logger.error(f"❌ OpenAI APIError for convo_id {convo_id}: {str(e)}", exc_info=True)
        ai_reply = "Sorry, I encountered an issue while processing your request."
    except AuthenticationError as e:
        logger.error(f"❌ OpenAI AuthenticationError for convo_id {convo_id}: {str(e)} (Check API Key)", exc_info=True)
        ai_reply = "There's an issue with my configuration. Please notify an administrator."
    except Exception as e:
        logger.error(f"❌ Unexpected error in get_ai_response for convo_id {convo_id}: {str(e)}", exc_info=True)
        ai_reply = "An unexpected error occurred. I've logged the issue."
    
    processing_time = time.time() - start_time_ai
    logger.info(f"GET_AI_RESPONSE for convo_id {convo_id} completed in {processing_time:.2f}s. Intent: {detected_intent}, Handoff: {handoff_triggered}")
    return ai_reply, detected_intent, handoff_triggered