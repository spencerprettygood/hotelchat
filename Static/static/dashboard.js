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
    const response = await fetch("/messages");
    if (!response.ok) return;

    const messages = await response.json();
    chatBox.innerHTML = "";

    messages.forEach(msg => {
        const messageElement = document.createElement("div");
        messageElement.classList.add("message", msg.sender === "user" ? "user-message" : "agent-message");
        messageElement.textContent = msg.message;
        chatBox.appendChild(messageElement);
    });

    chatBox.scrollTop = chatBox.scrollHeight; // Auto-scroll to latest message
}

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
