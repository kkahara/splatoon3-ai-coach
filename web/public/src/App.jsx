import { Route, Routes } from "react-router-dom";
import { LanguageSwitch } from "./LanguageSwitch.jsx";
import { AdminCoachingPage } from "./pages/AdminCoaching.jsx";
import { AdminFailuresPage } from "./pages/AdminFailures.jsx";
import { AdminLayout } from "./pages/AdminLayout.jsx";
import { AdminLlmPage } from "./pages/AdminLlm.jsx";
import { AdminPipelinePage } from "./pages/AdminPipeline.jsx";
import { AdminProductPage } from "./pages/AdminProduct.jsx";
import { AdminVersionsPage } from "./pages/AdminVersions.jsx";
import { AdminOverviewPage } from "./pages/AdminOverview.jsx";
import { AdminPricePage } from "./pages/AdminPrice.jsx";
import { AdminRequestDetailPage } from "./pages/AdminRequestDetail.jsx";
import { AdminRequestsPage } from "./pages/AdminRequests.jsx";
import { AdminUserDetailPage } from "./pages/AdminUserDetail.jsx";
import { AdminUsersPage } from "./pages/AdminUsers.jsx";
import { CoachingPage } from "./pages/Coaching.jsx";
import { ConfirmNoticesPage } from "./pages/ConfirmNotices.jsx";
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
      <Route path="/confirm-notices/:token" element={<ConfirmNoticesPage />} />
      <Route path="/coaching" element={<CoachingPage />} />
      <Route path="/coaching/:id" element={<ReviewPage />} />
      <Route path="/review/:token" element={<ReviewPage />} />
      <Route path="/admin" element={<AdminLayout />}>
        <Route index element={<AdminOverviewPage />} />
        <Route path="price" element={<AdminPricePage />} />
        <Route path="pipeline" element={<AdminPipelinePage />} />
        <Route path="coaching" element={<AdminCoachingPage />} />
        <Route path="versions" element={<AdminVersionsPage />} />
        <Route path="llm" element={<AdminLlmPage />} />
        <Route path="product" element={<AdminProductPage />} />
        <Route path="failures" element={<AdminFailuresPage />} />
        <Route path="requests" element={<AdminRequestsPage />} />
        <Route path="requests/:id" element={<AdminRequestDetailPage />} />
        <Route path="users" element={<AdminUsersPage />} />
        <Route path="users/:id" element={<AdminUserDetailPage />} />
      </Route>
    </Routes>
  );
}
