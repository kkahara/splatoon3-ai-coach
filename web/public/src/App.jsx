import { Route, Routes } from "react-router-dom";
import { ReviewPage } from "./pages/Review.jsx";
import { SubmitPage } from "./pages/Submit.jsx";

export function App() {
  return (
    <Routes>
      <Route path="/" element={<SubmitPage />} />
      <Route path="/review/:token" element={<ReviewPage />} />
    </Routes>
  );
}
