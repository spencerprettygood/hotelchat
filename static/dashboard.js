const chatBox = document.getElementById("chatBox");
const conversationList = document.getElementById("conversationList");
const notificationSound = new Audio('/static/notification.mp3');
let socket = null;
let isLoading = false;

document.addEventListener("DOMContentLoaded", function () {
    console.log("✅ Page loaded at:", new Date().toLocaleTimeString());
    checkLogin();  // Always check on load
});

async function checkLogin() {
    const agent = localStorage.getItem("agent");
    console.log("🔄 Checking login state at:", new Date().toLocaleTimeString());
    
    // Verify session with server before proceeding
    if (agent) {
        try {
            console.log("🔄 Verifying session for agent:", agent);
            const response = await fetch("/conversations", { 
                method: "GET",
                credentials: 'include'  // Include session cookies
            });
            if (response.ok) {
                console.log("✅ Session valid, loading dashboard");
                document.getElementById("loginPage").style.display = "none";
                document.getElementById("dashboard").style.display = "block";
                if (!socket) {
                    console.log("🔌 Initializing WebSocket");
                    listenForNewMessages();
                } else {
                    console.log("🔌 WebSocket already exists");
                }
                loadConversations();
            } else {
                console.log("❌ Session invalid, clearing agent and showing login");
                localStorage.removeItem("agent");
                showLoginPage();
            }
        } catch (error) {
            console.error("❌ Error verifying session:", error);
            localStorage.removeItem("agent");
            showLoginPage();
        }
    } else {
        console.log("🔒 No agent in localStorage, showing login");
        showLoginPage();
    }
}

function showLoginPage() {
    document.getElementById("loginPage").style.display = "flex";
    document.getElementById("dashboard").style.display = "none";
    if (socket) {
        console.log("🔌 Disconnecting WebSocket");
        socket.disconnect();
        socket = null;
    }
    chatBox.innerHTML = "";
    conversationList.innerHTML = "";
}

async function login() {
    const username = document.getElementById("username").value;
    const password = document.getElementById("password").value;
    if (!username || !password) {
        alert("Please enter both username and password.");
        return;
    }
    try {
        console.log("🔄 Attempting login...");
        const response = await fetch("/login", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ username, password }),
            credentials: 'include'  // Ensure session cookies are sent
        });
        const data = await response.json();
        if (response.ok) {
            localStorage.setItem("agent", data.agent);
            console.log("✅ Login successful, agent:", data.agent);
            checkLogin();
        } else {
            console.error("❌ Login failed:", data.message);
            alert(data.message || "Login failed.");
        }
    } catch (error) {
        console.error("❌ ERROR: Login failed", error);
        alert("Error connecting to server.");
    }
}

function logout() {
    fetch("/logout", { 
        method: "POST",
        credentials: 'include',
        headers: { "Content-Type": "application/json" }
    })
    .then(response => {
        if (response.ok) {
            console.log("✅ Logout successful");
            localStorage.removeItem("agent");
            showLoginPage();  // Immediately show login page
            window.location.reload();  // Force full reset
        } else {
            console.error("❌ Logout failed:", response.status);
            alert("Logout failed, please try again.");
        }
    })
    .catch(error => {
        console.error("Error during logout:", error);
        localStorage.removeItem("agent");
        showLoginPage();
    });
}

async function loadConversations(filter = 'all') {
    if (isLoading) {
        console.log("🔄 Skipping loadConversations, already in progress");
        return;
    }
    isLoading = true;
    try {
        console.log("🔄 Loading conversations with filter:", filter);
        const response = await fetch("/conversations", { credentials: 'include' });
        if (!response.ok) throw new Error("Failed to fetch conversations: " + response.status);
        const conversations = await response.json();
        conversationList.innerHTML = "";
        let unassignedCount = 0, yourCount = 0, teamCount = 0;

        conversations.forEach(convo => {
            const currentAgent = localStorage.getItem("agent");
            if (filter === 'unassigned' && convo.assigned_agent) return;
            if (filter === 'you' && convo.assigned_agent !== currentAgent) return;
            if (filter === 'team' && (!convo.assigned_agent || convo.assigned_agent === currentAgent)) return;

            const convoItem = document.createElement("div");
            convoItem.classList.add("conversation-item");
            convoItem.onclick = () => loadChat(convo.id, convo.username);
            const avatar = document.createElement("div");
            avatar.classList.add("conversation-avatar");
            avatar.textContent = convo.username.charAt(0).toUpperCase();
            const details = document.createElement("div");
            details.classList.add("conversation-details");
            const name = document.createElement("div");
            name.classList.add("name");
            name.textContent = `${convo.username} (${convo.channel})` + (convo.assigned_agent ? ` (${convo.assigned_agent})` : '');
            const preview = document.createElement("div");
            preview.classList.add("preview");
            preview.textContent = convo.latest_message || "No messages yet";
            details.appendChild(name);
            details.appendChild(preview);
            convoItem.appendChild(avatar);
            convoItem.appendChild(details);
            conversationList.appendChild(convoItem);

            if (!convo.assigned_agent) unassignedCount++;
            else if (convo.assigned_agent === currentAgent) yourCount++;
            else teamCount++;
        });

        document.getElementById("unassignedCount").textContent = unassignedCount;
        document.getElementById("yourCount").textContent = yourCount;
        document.getElementById("teamCount").textContent = teamCount;
        document.getElementById("allCount").textContent = conversations.length;
        console.log("✅ Conversations loaded, count:", conversations.length);
    } catch (error) {
        console.error("❌ Error loading conversations:", error);
    } finally {
        isLoading = false;
    }
}

async function loadChat(convoId, username) {
    if (isLoading) {
        console.log("🔄 Skipping loadChat, already in progress");
        return;
    }
    isLoading = true;
    try {
        console.log("🔄 Loading chat for convo ID:", convoId);
        const response = await fetch(`/messages?conversation_id=${convoId}`, { credentials: 'include' });
        if (!response.ok) throw new Error("Failed to load messages: " + response.status);
        const messages = await response.json();
        chatBox.innerHTML = "";
        messages.forEach(msg => {
            const messageElement = document.createElement("div");
            messageElement.classList.add("message", msg.sender === "user" ? "user-message" : "agent-message");
            messageElement.innerHTML = `<p>${msg.message}</p><span class="message-timestamp">${new Date(msg.timestamp).toLocaleTimeString()}</span>`;
            chatBox.appendChild(messageElement);
        });
        chatBox.scrollTop = chatBox.scrollHeight;
        document.getElementById("clientName").textContent = username;
        currentConvoId = convoId;
        console.log("✅ Chat loaded, message count:", messages.length);
    } catch (error) {
        console.error("❌ Error loading chat:", error);
    } finally {
        isLoading = false;
    }
}

let currentConvoId = null;

async function sendMessage() {
    const messageInput = document.getElementById("messageInput");
    const message = messageInput.value.trim();
    if (!message || !currentConvoId) {
        console.log("⚠️ No message or convo ID, skipping send");
        return;
    }
    addMessage(message, "user");
    messageInput.value = "";
    try {
        console.log("🔄 Sending message:", message);
        const response = await fetch("/chat", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ conversation_id: currentConvoId, message }),
            credentials: 'include'
        });
        const data = await response.json();
        addMessage(data.reply, "ai");
        console.log("✅ Message sent, reply received:", data.reply);
    } catch (error) {
        console.error("❌ Error sending message:", error);
    }
}

function addMessage(content, sender) {
    const messageElement = document.createElement("div");
    messageElement.classList.add("message", sender === "user" ? "user-message" : "agent-message");
    messageElement.innerHTML = `<p>${content}</p><span class="message-timestamp">${new Date().toLocaleTimeString()}</span>`;
    chatBox.appendChild(messageElement);
    chatBox.scrollTop = chatBox.scrollHeight;
}

function listenForNewMessages() {
    if (socket) {
        console.log("🔄 WebSocket already connected, skipping");
        return;
    }
    socket = io('https://hotel-chatbot-1qj5.onrender.com', { 
        transports: ["websocket"],
        reconnection: false
    });
    socket.on("connect", () => {
        console.log("✅ WebSocket connected at:", new Date().toLocaleTimeString());
    });
    socket.on("connect_error", (error) => {
        console.error("❌ WebSocket connection error:", error);
    });
    socket.on("new_message", (data) => {
        console.log("📩 New message received:", data);
        if (data.convo_id === currentConvoId) {
            addMessage(data.message, data.sender);
        }
        loadConversations();
        try { notificationSound.play(); } catch (e) { console.log("Notification sound failed:", e); }
    });
    socket.on("handoff", (data) => {
        console.log("🔔 Handoff event:", data);
        alert(`${data.agent} took over chat with ${data.user}`);
        loadConversations();
    });
}

const messageInput = document.getElementById("messageInput");
messageInput.removeEventListener("keypress", handleKeypress);
messageInput.addEventListener("keypress", handleKeypress);
function handleKeypress(event) {
    if (event.key === "Enter") sendMessage();
}

async function handoff() {
    const convoId = prompt("Enter the conversation ID to take over:");
    if (!convoId) return;
    try {
        console.log("🔄 Attempting handoff for convo ID:", convoId);
        const response = await fetch("/handoff", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ conversation_id: convoId }),
            credentials: 'include'
        });
        const data = await response.json();
        alert(data.message);
        loadConversations();
        console.log("✅ Handoff successful");
    } catch (error) {
        console.error("❌ Error during handoff:", error);
        alert("Failed to assign chat.");
    }
}

function filterByChannel(channel) {
    console.log(`Filtering by ${channel} - not implemented yet`);
}

setInterval(() => {
    if (!isLoading) {
        console.log("🔄 Polling conversations");
        loadConversations();
    }
}, 10000);
