// ✅ Define Global Variables
const chatBox = document.getElementById("chatBox");
const conversationList = document.getElementById("conversationList");
const conversationsTab = document.getElementById("conversationsTab");
const clientName = document.getElementById("clientName");
const clientContact = document.getElementById("clientContact");
const bettingType = document.getElementById("bettingType");
const notificationSound = new Audio('/static/notification.mp3');

// ✅ Ensure login is checked on page load
document.addEventListener("DOMContentLoaded", function () {
    checkLogin();
    listenForNewMessages();
});

// ✅ Check if an agent is logged in
function checkLogin() {
    const agent = localStorage.getItem("agent");

    if (agent) {
        document.getElementById("loginPage").style.display = "none";
        document.getElementById("dashboard").style.display = "block";
        loadConversations();
    } else {
        document.getElementById("loginPage").style.display = "flex";
        document.getElementById("dashboard").style.display = "none";
    }
}

// ✅ Agent Login
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
            localStorage.setItem("agent", username);
            document.getElementById("loginPage").style.display = "none";
            document.getElementById("dashboard").style.display = "block";
            loadConversations();
        } else {
            alert(data.message || "Login failed.");
        }
    } catch (error) {
        alert("Error connecting to server.");
        console.error("Login error:", error);
    }
}

// ✅ Agent Logout
function logout() {
    localStorage.removeItem("agent");
    checkLogin();
}

// ✅ Fetch and Display Conversations
async function loadConversations() {
    try {
        const response = await fetch("/conversations");
        if (!response.ok) throw new Error("Failed to fetch conversations");

        const conversations = await response.json();
        conversationList.innerHTML = "";

        conversations.forEach(convo => {
            const convoItem = document.createElement("li");
            convoItem.classList.add("conversation-item");
            convoItem.innerHTML = `<strong>${convo.username}</strong> <br> ${convo.latest_message}`;
            convoItem.onclick = () => loadChat(convo.id, convo.username);
            conversationList.appendChild(convoItem);
        });
    } catch (error) {
        console.error("Error loading conversations:", error);
    }
}

// ✅ Show Conversations when clicking the tab
conversationsTab.addEventListener("click", function (event) {
    event.preventDefault();
    loadConversations();
});

// ✅ Load chat messages when clicking a conversation
async function loadChat(convoId, username) {
    try {
        const response = await fetch(`/messages?conversation_id=${convoId}`);
        if (!response.ok) throw new Error("Failed to load messages");

        const messages = await response.json();
        chatBox.innerHTML = "";

        messages.forEach(msg => {
            const messageElement = document.createElement("div");
            messageElement.classList.add(msg.sender === "user" ? "user-message" : "agent-message");
            messageElement.textContent = msg.message;
            chatBox.appendChild(messageElement);
        });

        chatBox.scrollTop = chatBox.scrollHeight; // Auto-scroll
        updateClientInfo(username);
    } catch (error) {
        console.error("Error loading chat:", error);
    }
}

// ✅ Send Message Function
async function sendMessage() {
    const messageInput = document.getElementById("messageInput");
    const message = messageInput.value.trim();

    if (!message) return;

    addMessage(message, "user");
    messageInput.value = "";

    try {
        const response = await fetch("/chat", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ message }),
        });

        const data = await response.json();
        addMessage(data.reply, "agent");
    } catch (error) {
        console.error("Error sending message:", error);
    }
}

// ✅ Allow Sending Message by Pressing Enter
document.getElementById("messageInput").addEventListener("keypress", function (event) {
    if (event.key === "Enter") {
        sendMessage();
    }
});

// ✅ Add Message to Chat
function addMessage(content, sender) {
    const messageElement = document.createElement("div");
    messageElement.classList.add("message", sender === "user" ? "user-message" : "agent-message");
    messageElement.innerHTML = `
        <p>${content}</p>
        <span class="timestamp">${new Date().toLocaleTimeString()}</span>
    `;
    chatBox.appendChild(messageElement);
    chatBox.scrollTop = chatBox.scrollHeight;
}

// ✅ Listen for New Messages in Real-Time
async function listenForNewMessages() {
    const socket = io({ transports: ["polling"] });

    socket.on("new_message", function (data) {
        fetchMessages();
        notificationSound.play();
        alert("New Message from: " + data.user);
    });

    socket.on("handoff", function (data) {
        alert(data.agent + " took over chat with " + data.user);
    });
}

// ✅ Human Handoff (Agent Takes Over Chat)
async function handoff() {
    const user_id = prompt("Enter the user ID to take over:");
    if (!user_id) return;

    const response = await fetch("/handoff", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ user_id })
    });

    const data = await response.json();
    alert(data.message);
}

// ✅ Update Client Info Panel
function updateClientInfo(name) {
    clientName.textContent = name || "-";
}

// ✅ Run Initial Functions
document.addEventListener("DOMContentLoaded", function() {
    if (localStorage.getItem("agent")) {
        document.getElementById("loginPage").style.display = "none";
        document.getElementById("dashboard").style.display = "block";
        loadConversations();
    }
    listenForNewMessages();
});

// ✅ Auto-Update Conversations Every 5 Seconds
setInterval(loadConversations, 5000);

