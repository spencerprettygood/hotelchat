import os
import time
import openai
from openai import OpenAI, RateLimitError, APIError, AuthenticationError, APITimeoutError
import logging
from circuitbreaker import CircuitBreaker
from dotenv import load_dotenv
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize OpenAI client
try:
    # Use a more descriptive variable name to avoid conflict with the module name
    openai_client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
    logger.info("✅ OpenAI client initialized successfully.")
except KeyError:
    logger.error("❌ OPENAI_API_KEY environment variable not set.")
    openai_client = None
except Exception as e:
    logger.error(f"❌ Failed to initialize OpenAI client: {e}")
    openai_client = None

# Circuit Breaker for OpenAI API
circuit_breaker = CircuitBreaker(failure_threshold=3, recovery_timeout=60)

@circuit_breaker.call
@retry(
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=2, min=4, max=30),
    retry=retry_if_exception_type((APITimeoutError, RateLimitError, APIError))
)
def get_ai_response(convo_id, username, conversation_history, user_message, chat_id, channel, language="en", correlation_id=None):
    """
    Synchronous function to get AI responses using the OpenAI API.
    Returns: ai_reply, detected_intent, handoff_triggered
    Enhanced with circuit breaker, retry, and production-grade error handling.
    """
    if not openai_client:
        logger.error("OpenAI client is not initialized. Cannot get AI response.")
        return "I am currently unable to process requests.", None, False

    if not correlation_id:
        correlation_id = f"convo-{convo_id}-{int(time.time())}"
    start_time_ai = time.time()
    logger.info(f"[CID:{correlation_id}] GET_AI_RESPONSE initiated for convo_id: {convo_id}, user: {username}, channel: {channel}, lang: {language}. Message: '{user_message[:50]}...'")
    
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
    
    system_prompt = f"You are a helpful assistant for Amapola Resort. Current language for response: {language}."
    messages_for_openai = [
        {"role": "system", "content": system_prompt}
    ] + conversation_history
    
    ai_reply = None
    detected_intent = None
    handoff_triggered = False
    request_start_time = time.time()
    try:
        logger.info(f"[CID:{correlation_id}] Calling OpenAI API for convo_id {convo_id}. Model: gpt-4o-mini. History length: {len(messages_for_openai)}")
        response = openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=messages_for_openai, # type: ignore
            max_tokens=300,
            temperature=0.7
        )
        
        if response.choices and response.choices[0].message and response.choices[0].message.content:
            ai_reply = response.choices[0].message.content.strip()
        else:
            ai_reply = "I could not generate a response at this time."
            logger.error(f"[CID:{correlation_id}] OpenAI response was empty or invalid.")

        usage = response.usage
        if usage:
            processing_time = (time.time() - request_start_time) * 1000
            logger.info(f"[CID:{correlation_id}] [OpenAI Response] Convo ID: {convo_id} - Reply: '{ai_reply[:100]}...' - Tokens: P{usage.prompt_tokens}/C{usage.completion_tokens}/T{usage.total_tokens} - Time: {processing_time:.2f}ms")
        
        # Basic intent detection (example - can be expanded)
        if "book a room" in user_message.lower() or "reservation" in user_message.lower():
            detected_intent = "booking_inquiry"
        if "human" in user_message.lower() or "agent" in user_message.lower() or "speak to someone" in user_message.lower():
            handoff_triggered = True
            logger.info(f"Handoff to human agent triggered by user message for convo_id {convo_id}")
            
    except RateLimitError as e:
        logger.error(f"❌ OpenAI RateLimitError for convo_id {convo_id}: {str(e)}", exc_info=True)
        ai_reply = "I'm currently experiencing high demand. Please try again in a moment."
        raise # Reraise to trigger retry
    except APITimeoutError as e:
        logger.error(f"❌ OpenAI APITimeoutError for convo_id {convo_id}: {str(e)}", exc_info=True)
        ai_reply = "I'm having trouble connecting to my brain right now. Please try again shortly."
        raise # Reraise to trigger retry
    except APIError as e: # General API error
        logger.error(f"❌ OpenAI APIError for convo_id {convo_id}: {str(e)}", exc_info=True)
        ai_reply = "Sorry, I encountered an issue while processing your request."
        raise # Reraise to trigger retry
    except AuthenticationError as e:
        logger.critical(f"❌ OpenAI AuthenticationError for convo_id {convo_id}: {str(e)} (Check API Key)", exc_info=True)
        ai_reply = "There's an issue with my configuration. Please notify an administrator."
        # Do not retry on auth errors
    except Exception as e:
        logger.error(f"❌ Unexpected error in get_ai_response for convo_id {convo_id}: {str(e)}", exc_info=True)
        ai_reply = "An unexpected error occurred. I've logged the issue."
    
    processing_time = time.time() - start_time_ai
    logger.info(f"GET_AI_RESPONSE for convo_id {convo_id} completed in {processing_time:.2f}s. Intent: {detected_intent}, Handoff: {handoff_triggered}")
    return ai_reply, detected_intent, handoff_triggered
