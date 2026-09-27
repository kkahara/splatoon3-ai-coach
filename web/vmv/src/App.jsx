import { NavLink, Route, Routes } from "react-router-dom";
import { AnalyzePage } from "./pages/Analyze.jsx";
import { LibraryPage } from "./pages/Library.jsx";
import { RunPage } from "./pages/Run.jsx";

export function App() {
  return (
    <>
      <header className="top">
        <strong>Analysis platform</strong>
        <nav>
          <NavLink to="/" end>Library</NavLink>
          <NavLink to="/analyze">Analyze</NavLink>
        </nav>
      </header>
      <main>
        <Routes>
          <Route path="/" element={<LibraryPage />} />
          <Route path="/analyze" element={<AnalyzePage />} />
          <Route path="/runs/:id" element={<RunPage />} />
        </Routes>
      </main>
    </>
  );
}
