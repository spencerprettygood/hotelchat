const chatBox = document.getElementById("chatBox");
const conversationList = document.getElementById("conversationList");
const notificationSound = new Audio('/static/notification.mp3');
let socket = null;

document.addEventListener("DOMContentLoaded", function () {
    checkLogin();
});

function checkLogin() {
    const agent = localStorage.getItem("agent");
    if (agent) {
        document.getElementById("loginPage").style.display = "none";
        document.getElementById("dashboard").style.display = "block";
        if (!socket) listenForNewMessages();
        loadConversations();
    } else {
        document.getElementById("loginPage").style.display = "flex";
        document.getElementById("dashboard").style.display = "none";
        if (socket) {
            socket.disconnect();
            socket = null;
        }
        chatBox.innerHTML = "";
        conversationList.innerHTML = "";
    }
}

async function login() {
    const username = document.getElementById("username").value;
    const password = document.getElementById("password").value;
    if (!username || !password) {
        alert("Please enter both username and password.");
        return;
    }
    try {
        const response = await fetch("/login", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ username, password }),
        });
        const data = await response.json();
        if (response.ok) {
            localStorage.setItem("agent", data.agent);
            checkLogin();
        } else {
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
            localStorage.removeItem("agent");
            checkLogin();
        } else {
            console.error("Logout failed:", response.status);
        }
    })
    .catch(error => console.error("Error during logout:", error));
}

async function loadConversations(filter = 'all') {
    try {
        const response = await fetch("/conversations");
        if (!response.ok) throw new Error("Failed to fetch conversations");
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
            name.textContent = convo.username + (convo.assigned_agent ? ` (${convo.assigned_agent})` : '');
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
    } catch (error) {
        console.error("Error loading conversations:", error);
    }
}

async function loadChat(convoId, username) {
    try {
        const response = await fetch(`/messages?conversation_id=${convoId}`);
        if (!response.ok) throw new Error("Failed to load messages");
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
    } catch (error) {
        console.error("Error loading chat:", error);
    }
}

let currentConvoId = null;

async function sendMessage() {
    const messageInput = document.getElementById("messageInput");
    const message = messageInput.value.trim();
    if (!message || !currentConvoId) return;
    addMessage(message, "user");
    messageInput.value = "";
    try {
        const response = await fetch("/chat", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ conversation_id: currentConvoId, message }),
        });
        const data = await response.json();
        addMessage(data.reply, "ai");
    } catch (error) {
        console.error("Error sending message:", error);
    }
}

document.getElementById("messageInput").addEventListener("keypress", function (event) {
    if (event.key === "Enter") sendMessage();
});

function addMessage(content, sender) {
    const messageElement = document.createElement("div");
    messageElement.classList.add("message", sender === "user" ? "user-message" : "agent-message");
    messageElement.innerHTML = `<p>${content}</p><span class="message-timestamp">${new Date().toLocaleTimeString()}</span>`;
    chatBox.appendChild(messageElement);
    chatBox.scrollTop = chatBox.scrollHeight;
}

function listenForNewMessages() {
    if (socket) return;
    socket = io('https://hotel-chatbot-1qj5.onrender.com', { 
        transports: ["websocket"],
        reconnection: true,
        reconnectionAttempts: 5
    });
    socket.on("connect", () => {
        console.log("✅ WebSocket connected");
    });
    socket.on("connect_error", (error) => {
        console.error("❌ WebSocket connection error:", error);
    });
    socket.on("new_message", (data) => {
        if (data.convo_id === currentConvoId) {
            addMessage(data.message, data.sender);
        }
        loadConversations();
        try { notificationSound.play(); } catch (e) { console.log("Notification sound failed:", e); }
    });
    socket.on("handoff", (data) => {
        alert(`${data.agent} took over chat with ${data.user}`);
        loadConversations();
    });
}

async function handoff() {
    const convoId = prompt("Enter the conversation ID to take over:");
    if (!convoId) return;
    try {
        const response = await fetch("/handoff", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ conversation_id: convoId }),
        });
        const data = await response.json();
        alert(data.message);
        loadConversations();
    } catch (error) {
        console.error("Error during handoff:", error);
        alert("Failed to assign chat.");
    }
}

function filterByChannel(channel) {
    console.log(`Filtering by ${channel} - not implemented yet`);
}

setInterval(() => loadConversations(), 5000);
