import { Route, Routes } from "react-router-dom";
import { LanguageSwitch } from "./LanguageSwitch.jsx";
import { CoachingPage } from "./pages/Coaching.jsx";
import { ForgotPage } from "./pages/Forgot.jsx";
import { LoginPage } from "./pages/Login.jsx";
import { RegisterPage } from "./pages/Register.jsx";
import { ResetPage } from "./pages/Reset.jsx";
import { ReviewPage } from "./pages/Review.jsx";
import { SubmitPage } from "./pages/Submit.jsx";
import { VerifyPage } from "./pages/Verify.jsx";

export function App() {
  return (
    <>
      <LanguageSwitch />
      <AppRoutes />
    </>
  );
}

function AppRoutes() {
  return (
    <Routes>
      <Route path="/" element={<SubmitPage />} />
      <Route path="/register" element={<RegisterPage />} />
      <Route path="/login" element={<LoginPage />} />
      <Route path="/forgot" element={<ForgotPage />} />
      <Route path="/reset/:token" element={<ResetPage />} />
      <Route path="/verify/:token" element={<VerifyPage />} />
      <Route path="/coaching" element={<CoachingPage />} />
      <Route path="/coaching/:id" element={<ReviewPage />} />
      <Route path="/review/:token" element={<ReviewPage />} />
    </Routes>
  );
}
