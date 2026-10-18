import ChatPage from "./Chat";
import StaffApp from "./Staff";

// Two routes only, so a tiny pathname check is enough. FastAPI serves index.html for /staff too.
// Hiding the staff UI is NOT the security control: every staff API call is checked on the server.
export default function App() {
  const isStaff = window.location.pathname.replace(/\/+$/, "") === "/staff";
  return isStaff ? <StaffApp /> : <ChatPage />;
}
