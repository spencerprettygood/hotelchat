/**
 * Amapola Resort Chat Dashboard
 * Handles conversation listing, real-time messaging, and agent interactions
 */

document.addEventListener('DOMContentLoaded', function() {
    // DOM elements
    const conversationList = document.getElementById('conversationList');
    const chatArea = document.getElementById('chatArea');
    const refreshConversationsBtn = document.getElementById('refreshConversationsBtn');
    
    // Templates
    const conversationItemTemplate = document.getElementById('conversationItemTemplate');
    const activeChatTemplate = document.getElementById('activeChatTemplate');
    const messageTemplate = document.getElementById('messageTemplate');
    
    // State
    let activeConversationId = null;
    let activeConversationChatId = null;
    let activeConversationChannel = null;
    let socket = null;
    
    // Initialize Socket.IO connection
    initializeSocketIO();
    
    // Load conversations on page load
    fetchConversations();
    
    // Event Listeners
    refreshConversationsBtn.addEventListener('click', fetchConversations);
    
    /**
     * Initialize Socket.IO connection
     */
    function initializeSocketIO() {
        socket = io();
        
        socket.on('connect', function() {
            console.log('Connected to Socket.IO server');
        });
        
        socket.on('disconnect', function() {
            console.log('Disconnected from Socket.IO server');
        });
        
        socket.on('new_message', function(data) {
            console.log('New message received:', data);
            
            if (activeConversationId && data.convo_id == activeConversationId) {
                addMessageToChat(data.message, data.sender, data.username, data.timestamp);
                
                // Scroll to bottom
                const messagesContainer = document.querySelector('.messages-container');
                if (messagesContainer) {
                    messagesContainer.scrollTop = messagesContainer.scrollHeight;
                }
            }
            
            // If this is a new message in a conversation not being viewed, highlight it
            if (data.convo_id != activeConversationId) {
                const conversationItem = document.querySelector(`.conversation-item[data-convo-id="${data.convo_id}"]`);
                if (conversationItem) {
                    conversationItem.classList.add('bg-light');
                } else {
                    // If the conversation isn't in the list yet, refresh the list
                    fetchConversations();
                }
            }
        });
        
        socket.on('error', function(error) {
            console.error('Socket.IO error:', error);
            alert('Communication error: ' + error.message);
        });
    }
    
    /**
     * Fetch conversations from the server
     */
    function fetchConversations() {
        fetch('/get_conversations')
            .then(response => response.json())
            .then(conversations => {
                displayConversations(conversations);
            })
            .catch(error => {
                console.error('Error fetching conversations:', error);
                alert('Failed to load conversations. Please try again.');
            });
    }
    
    /**
     * Display conversations in the sidebar
     */
    function displayConversations(conversations) {
        conversationList.innerHTML = '';
        
        if (conversations.length === 0) {
            const emptyMessage = document.createElement('div');
            emptyMessage.className = 'p-3 text-center text-muted';
            emptyMessage.textContent = 'No active conversations';
            conversationList.appendChild(emptyMessage);
            return;
        }
        
        conversations.forEach(conversation => {
            const clone = document.importNode(conversationItemTemplate.content, true);
            const conversationItem = clone.querySelector('.conversation-item');
            
            conversationItem.dataset.convoId = conversation.id;
            conversationItem.dataset.chatId = conversation.chat_id || '';
            conversationItem.dataset.channel = conversation.channel || 'unknown';
            
            // Set username
            clone.querySelector('.convo-username').textContent = conversation.username;
            
            // Set channel badge
            const channelBadge = clone.querySelector('.channel-badge');
            channelBadge.textContent = conversation.channel;
            if (conversation.channel === 'whatsapp') {
                channelBadge.classList.add('whatsapp-badge');
            } else {
                channelBadge.classList.add('webchat-badge');
            }
            
            // Show needs agent badge if needed
            if (conversation.needs_agent === 1) {
                clone.querySelector('.needs-agent-badge').classList.remove('d-none');
            }
            
            // Set active class if this is the active conversation
            if (activeConversationId && conversation.id == activeConversationId) {
                conversationItem.classList.add('active');
            }
            
            // Add click event to load conversation
            conversationItem.addEventListener('click', function() {
                loadConversation(conversation.id, conversationItem.dataset.chatId, conversationItem.dataset.channel);
                
                // Remove active class from all conversations
                document.querySelectorAll('.conversation-item').forEach(item => {
                    item.classList.remove('active');
                    item.classList.remove('bg-light'); // Remove new message highlight
                });
                
                // Add active class to this conversation
                conversationItem.classList.add('active');
            });
            
            conversationList.appendChild(clone);
        });
    }
    
    /**
     * Load a conversation's messages
     */
    function loadConversation(conversationId, chatId, channel) {
        // Set active conversation
        activeConversationId = conversationId;
        activeConversationChatId = chatId;
        activeConversationChannel = channel;
        
        // Leave previous conversation room if any
        if (socket && activeConversationId) {
            socket.emit('leave_conversation', { conversation_id: activeConversationId });
        }
        
        // Join new conversation room
        if (socket) {
            socket.emit('join_conversation', { conversation_id: conversationId });
        }
        
        // Display active chat template
        chatArea.innerHTML = '';
        const clone = document.importNode(activeChatTemplate.content, true);
        chatArea.appendChild(clone);
        
        // Fetch and display messages
        fetchMessages(conversationId);
        
        // Set up message form
        const messageForm = chatArea.querySelector('.message-form');
        messageForm.addEventListener('submit', function(e) {
            e.preventDefault();
            sendMessage(conversationId);
        });
        
        // Set up refresh button
        const refreshButton = chatArea.querySelector('.refresh-messages');
        refreshButton.addEventListener('click', function() {
            fetchMessages(conversationId);
        });
    }
    
    /**
     * Fetch messages for a conversation
     */
    function fetchMessages(conversationId) {
        fetch(`/get_messages/${conversationId}`)
            .then(response => response.json())
            .then(data => {
                if (data.error) {
                    throw new Error(data.error);
                }
                
                // Set chat header
                const chatUsername = chatArea.querySelector('.chat-username');
                const chatChannel = chatArea.querySelector('.chat-channel');
                chatUsername.textContent = data.username;
                chatChannel.textContent = `Channel: ${activeConversationChannel}`;
                
                // Display messages
                const messagesContainer = chatArea.querySelector('.messages-container');
                messagesContainer.innerHTML = '';
                
                data.messages.forEach(message => {
                    addMessageToChat(
                        message.message,
                        message.sender,
                        message.sender === 'user' ? data.username : (message.sender === 'bot' ? 'AI Bot' : 'Agent'),
                        message.timestamp
                    );
                });
                
                // Scroll to bottom
                messagesContainer.scrollTop = messagesContainer.scrollHeight;
            })
            .catch(error => {
                console.error('Error fetching messages:', error);
                alert('Failed to load messages: ' + error.message);
            });
    }
    
    /**
     * Add a message to the chat display
     */
    function addMessageToChat(content, sender, username, timestamp) {
        const messagesContainer = chatArea.querySelector('.messages-container');
        const clone = document.importNode(messageTemplate.content, true);
        const messageEl = clone.querySelector('.message');
        
        // Set message class based on sender
        messageEl.classList.add(sender);
        
        // Set message content
        const messageContent = clone.querySelector('.message-content');
        messageContent.textContent = content;
        
        // Set message metadata
        const messageMeta = clone.querySelector('.message-meta');
        const messageTime = new Date(timestamp).toLocaleTimeString();
        messageMeta.textContent = `${username} • ${messageTime}`;
        
        messagesContainer.appendChild(clone);
    }
    
    /**
     * Send a message from the agent
     */
    function sendMessage(conversationId) {
        const messageInput = chatArea.querySelector('.message-text');
        const message = messageInput.value.trim();
        
        if (!message) return;
        
        // Clear input
        messageInput.value = '';
        
        // Send via Socket.IO
        if (socket) {
            socket.emit('agent_message', {
                convo_id: conversationId,
                message: message,
                chat_id: activeConversationChatId,
                channel: activeConversationChannel
            });
        } else {
            // Fallback to REST API if socket not available
            fetch('/send_message', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    convo_id: conversationId,
                    message: message,
                    chat_id: activeConversationChatId,
                    channel: activeConversationChannel
                })
            })
            .then(response => response.json())
            .then(data => {
                if (data.error) {
                    throw new Error(data.error);
                }
                console.log('Message sent:', data);
            })
            .catch(error => {
                console.error('Error sending message:', error);
                alert('Failed to send message: ' + error.message);
            });
        }
    }
    
    /**
     * Add event listeners for conversation rows
     */
    function setupConversationListeners() {
        // Listen for clicks on conversation rows or view buttons
        $(document).on('click', '.conversation-row, .view-conversation-btn', function(e) {
            // Prevent default if this is a link or button
            e.preventDefault();
            
            // Get conversation ID from data attribute or parent row
            let convoId;
            if ($(this).hasClass('conversation-row')) {
                convoId = $(this).data('convo-id');
            } else { // It's the view button
                convoId = $(this).closest('.conversation-row').data('convo-id');
            }
            
            if (convoId) {
                viewLiveMessages(convoId);
            }
        });
    }
    
    /**
     * View live messages page for a conversation
     */
    function viewLiveMessages(convoId) {
        // Navigate to the live messages page with the conversation ID
        window.location.href = `/live-messages?id=${convoId}`;
    }
});
