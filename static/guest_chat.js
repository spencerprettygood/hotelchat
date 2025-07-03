/**
 * Amapola Resort Guest Chat
 * Handles guest-side real-time messaging and session management.
 */

document.addEventListener('DOMContentLoaded', function() {
    // DOM elements
    const chatMessages = document.getElementById('chat-messages');
    const messageForm = document.getElementById('message-form');
    const messageInput = document.getElementById('message-input');
    const statusIndicator = document.getElementById('status-indicator');

    // State
    let socket = null;
    let chatId = localStorage.getItem('hotel_chat_id'); // Persist chat_id across sessions

    // Initialize Socket.IO connection
    initializeSocketIO();

    /**
     * Initialize Socket.IO connection and set up event listeners.
     */
    function initializeSocketIO() {
        socket = io({
            // Optional: add connection options if needed
        });

        socket.on('connect', () => {
            console.log('Connected to Socket.IO server.');
            statusIndicator.textContent = 'Connected';
            statusIndicator.classList.remove('text-danger');
            statusIndicator.classList.add('text-success');
            
            // If a chat session already exists, join its room to receive messages
            if (chatId) {
                socket.emit('join', { chat_id: chatId });
                console.log(`Re-joined room for chat_id: ${chatId}`);
            }
        });

        socket.on('disconnect', () => {
            console.warn('Disconnected from Socket.IO server.');
            statusIndicator.textContent = 'Disconnected';
            statusIndicator.classList.remove('text-success');
            statusIndicator.classList.add('text-danger');
        });

        // Listen for the server assigning a new session/chat_id
        socket.on('session_assigned', (data) => {
            if (data.chat_id) {
                chatId = data.chat_id;
                localStorage.setItem('hotel_chat_id', chatId);
                console.log(`Session assigned. Chat ID: ${chatId}`);
                
                // Join the room associated with this new chat ID
                socket.emit('join', { chat_id: chatId });
            }
        });

        // Listen for new messages from the server (AI or Agent)
        socket.on('new_message', (data) => {
            console.log('New message received:', data);
            // Ensure the message is for this chat session
            if (data.chat_id === chatId) {
                addMessageToChat(data.message, data.sender, data.username);
            }
        });

        socket.on('error', (error) => {
            console.error('Socket.IO error:', error);
            alert('A connection error occurred. Please refresh the page.');
        });
    }

    /**
     * Handle the submission of the message form.
     */
    messageForm.addEventListener('submit', function(e) {
        e.preventDefault();
        const message = messageInput.value.trim();

        if (!message) {
            return;
        }

        // Add the user's message to the UI immediately
        addMessageToChat(message, 'user', 'You');

        // Send the message to the server
        if (socket) {
            socket.emit('guest_message', {
                message: message,
                chat_id: chatId // Send current chat_id, will be null for the first message
            });
        }

        // Clear the input field
        messageInput.value = '';
    });

    /**
     * Add a message to the chat window.
     * @param {string} message - The message content.
     * @param {string} sender - The sender type ('user', 'bot', 'agent').
     * @param {string} username - The display name of the sender.
     */
    function addMessageToChat(message, sender, username) {
        const messageElement = document.createElement('div');
        messageElement.classList.add('message', `message-${sender}`);
        
        const senderElement = document.createElement('div');
        senderElement.classList.add('message-sender');
        senderElement.textContent = username;

        const contentElement = document.createElement('div');
        contentElement.classList.add('message-content');
        contentElement.textContent = message;

        messageElement.appendChild(senderElement);
        messageElement.appendChild(contentElement);

        chatMessages.appendChild(messageElement);

        // Scroll to the bottom of the chat window
        chatMessages.scrollTop = chatMessages.scrollHeight;
    }
});
