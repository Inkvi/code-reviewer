import { BrowserRouter, Routes, Route } from "react-router-dom";
import Layout from "./components/Layout";
import RepoList from "./pages/RepoList";
import PRList from "./pages/PRList";
import PRDetail from "./pages/PRDetail";
import PRHistory from "./pages/PRHistory";

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route element={<Layout />}>
          <Route path="/" element={<RepoList />} />
          <Route path="/:org/:repo" element={<PRList />} />
          <Route path="/:org/:repo/:number" element={<PRDetail />} />
          <Route
            path="/:org/:repo/:number/history"
            element={<PRHistory />}
          />
          <Route
            path="/:org/:repo/:number/history/:version"
            element={<PRDetail isHistorical />}
          />
        </Route>
      </Routes>
    </BrowserRouter>
  );
}
