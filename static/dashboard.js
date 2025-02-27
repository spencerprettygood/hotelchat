// Define Global Variables
const chatBox = document.getElementById("chatBox");
const agentList = document.getElementById("agentList");
const clientName = document.getElementById("clientName");
const clientContact = document.getElementById("clientContact");
const bettingType = document.getElementById("bettingType");

// ✅ Play notification sound when a new message arrives
const notificationSound = new Audio('/static/notification.mp3');

// ✅ Check login status on page load
document.addEventListener("DOMContentLoaded", function () {
    checkLogin();
});

// ✅ Function to check if an agent is logged in
function checkLogin() {
    const agent = localStorage.getItem("agent");
    if (agent) {
        document.getElementById("loginPage").style.display = "none";
        document.getElementById("dashboard").style.display = "block";
        fetchMessages(); // Load messages after login
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
        const response = await fetch("https://patroni.pythonanywhere.com/login", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ username, password }),
        });

        const data = await response.json();
        if (response.ok) {
            localStorage.setItem("agent", username);
            document.getElementById("loginPage").style.display = "none";
            document.getElementById("dashboard").style.display = "block";
            fetchMessages(); // Load chat messages after login
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
    fetch("/logout", { method: "POST" }).then(() => {
        localStorage.removeItem("agent"); // Remove stored agent
        checkLogin(); // Redirect back to login screen
    });
}

// ✅ Fetch and Display Messages
async function fetchMessages() {
    try {
        const response = await fetch("/messages", {
            method: "GET",
            credentials: "include",  // ✅ Ensures session cookies are sent
            headers: { "Content-Type": "application/json" },
        });

        if (!response.ok) {
            console.error("Failed to fetch messages:", response.status);
            return;
        }

        const messages = await response.json();
        chatBox.innerHTML = "";

        messages.forEach(msg => {
            const messageElement = document.createElement("div");
            messageElement.classList.add("message", msg.sender === "user" ? "user-message" : "agent-message");
            messageElement.textContent = msg.message;
            chatBox.appendChild(messageElement);
        });

        chatBox.scrollTop = chatBox.scrollHeight;  // Auto-scroll to latest message
    } catch (error) {
        console.error("Error fetching messages:", error);
    }
}
  
// ✅ Fetch and Display Conversations
async function fetchConversations() {
    try {
        const response = await fetch("/conversations"); // Fetch from backend
        if (!response.ok) throw new Error("Failed to fetch conversations");

        const conversations = await response.json();
        const conversationList = document.getElementById("conversationList");
        conversationList.innerHTML = ""; // Clear existing list

        conversations.forEach(convo => {
            const convoItem = document.createElement("li");
            convoItem.innerHTML = `<strong>${convo.username}</strong> <br> ${convo.latest_message}`;
            convoItem.classList.add("conversation-item");
            convoItem.onclick = () => loadChat(convo.id);
            conversationList.appendChild(convoItem);
        });
    } catch (error) {
        console.error("Error loading conversations:", error);
    }
}

// Function to show conversations when clicking on "Conversations"
function showConversations() {
    fetchConversations();
}

// Load chat when clicking on a conversation
async function loadChat(convoId) {
    try {
        const response = await fetch(`/messages?conversation_id=${convoId}`);
        if (!response.ok) throw new Error("Failed to load messages");

        const messages = await response.json();
        const chatBox = document.getElementById("chatBox");
        chatBox.innerHTML = "";

        messages.forEach(msg => {
            const messageElement = document.createElement("div");
            messageElement.classList.add(msg.sender === "user" ? "user-message" : "agent-message");
            messageElement.textContent = msg.message;
            chatBox.appendChild(messageElement);
        });

        chatBox.scrollTop = chatBox.scrollHeight; // Scroll to latest message
    } catch (error) {
        console.error("Error loading chat:", error);
    }
}


    const messages = await response.json();
    chatBox.innerHTML = `<h3>Chat with ${username}</h3>`; // Update chat header

    messages.forEach(msg => {
        const messageElement = document.createElement("div");
        messageElement.classList.add("message", msg.sender === "user" ? "user-message" : "agent-message");
        messageElement.textContent = msg.message;
        chatBox.appendChild(messageElement);
    });

    chatBox.scrollTop = chatBox.scrollHeight; // Auto-scroll
}


async function assignChat(convoId) {
    const response = await fetch("/assign_chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ convo_id: convoId }),
    });

    const data = await response.json();
    alert(data.message);

    // Refresh conversation list to reflect new assignment
    loadConversations();
}

async function loadConversations() {
    try {
        const response = await fetch("/conversations");
        const conversations = await response.json();
        const unassignedList = document.getElementById("unassignedList");
        const assignedList = document.getElementById("assignedList");

        unassignedList.innerHTML = "";
        assignedList.innerHTML = "";

        conversations.forEach(convo => {
            const convoItem = document.createElement("div");
            convoItem.classList.add("conversation-item");
            convoItem.innerHTML = `
                <div class="conversation-avatar">
                    <span class="avatar">${convo.initials}</span>
                </div>
                <div class="conversation-info">
                    <h4>${convo.username}</h4>
                    <p class="preview">${convo.latest_message}</p>
                </div>
            `;

            // If conversation is unassigned, allow agent to take it
            if (!convo.assigned_agent) {
                convoItem.onclick = () => assignChat(convo.id);
                unassignedList.appendChild(convoItem);
            } else if (convo.assigned_agent === localStorage.getItem("agent")) {
                assignedList.appendChild(convoItem);
            }
        });
    } catch (error) {
        console.error("Error loading conversations:", error);
    }
}



// ✅ Function to Add Messages with Timestamp
function addMessage(content, sender) {
    const messageElement = document.createElement("div");
    messageElement.classList.add("message", sender === "user" ? "user-message" : "agent-message");
    messageElement.innerHTML = `
        <p>${content}</p>
        <span class="timestamp">${new Date().toLocaleTimeString()}</span>
    `;
    chatBox.appendChild(messageElement);
    chatBox.scrollTop = chatBox.scrollHeight; // Auto-scroll to latest message
}

// ✅ Send Message Function with Typing Indicator
async function sendMessage() {
    const messageInput = document.getElementById("messageInput");
    const typingIndicator = document.getElementById("typingIndicator");
    const message = messageInput.value.trim();

    if (!message) return;

    addMessage(message, "user"); // Display user message
    messageInput.value = "";

    // Show Typing Indicator
    typingIndicator.style.display = "block";

    const response = await fetch("/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message })
    });

    const data = await response.json();

    // Hide Typing Indicator
    typingIndicator.style.display = "none";

    addMessage(data.reply, "agent"); // Display AI response
}


// ✅ Auto-Update Conversations Every 5 Seconds
setInterval(loadConversations, 5000);

// ✅ Run functions on page load
document.addEventListener("DOMContentLoaded", function () {
    const conversationsTab = document.getElementById("conversationsTab");
    const conversationPanel = document.getElementById("conversationPanel");

    if (conversationsTab) {
        conversationsTab.addEventListener("click", function (event) {
            event.preventDefault(); // Prevent default link behavior
            conversationPanel.style.display = "block"; // Show the panel
            loadConversations(); // Fetch and display conversations
        });
    }
});




// ✅ Notify Dashboard when a new message arrives
async function listenForNewMessages() {
const socket = io({
    transports: ['polling']
});

    socket.on("new_message", function(data) {
        fetchMessages();
        notificationSound.play(); // Play sound alert
        alert("New Message from: " + data.user); // Optional browser alert
    });

    socket.on("handoff", function(data) {
        alert(data.agent + " took over chat with " + data.user);
    });
}

// ✅ Human Handoff
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

// ✅ Auto Assign AI or Human
function detectHandoffCondition(message) {
    const triggers = ["human", "speak to an agent", "help me now"];
    return triggers.some(keyword => message.toLowerCase().includes(keyword));
}

// ✅ Update Client Info Panel
function updateClientInfo(name, contact, betting) {
    clientName.textContent = name || "-";
    clientContact.textContent = contact || "-";
    bettingType.textContent = betting || "-";
}

// ✅ Run initial functions
document.addEventListener("DOMContentLoaded", function() {
    if (localStorage.getItem("agent")) {
        document.getElementById("loginPage").style.display = "none";
        document.getElementById("dashboard").style.display = "block";
        fetchMessages();
    }
    listenForNewMessages();
});

// Fetch new messages every 5 seconds
setInterval(fetchMessages, 5000);
