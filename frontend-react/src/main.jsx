import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Route, Routes } from "react-router-dom";
import Layout from "./components/Layout.jsx";
import { AppStateProvider } from "./state/AppState.jsx";
import Dashboard from "./pages/Dashboard.jsx";
import CompanyForm from "./pages/CompanyForm.jsx";
import NoticeDetail from "./pages/NoticeDetail.jsx";
import Login from "./pages/Login.jsx";
import NotFound from "./pages/NotFound.jsx";
import "./styles/global.css";

createRoot(document.getElementById("root")).render(
  <StrictMode>
    <BrowserRouter>
      <AppStateProvider>
        <Routes>
          <Route element={<Layout />}>
            <Route index element={<Dashboard />} />
            <Route path="company" element={<CompanyForm />} />
            <Route path="login" element={<Login />} />
            <Route path="notices/:id" element={<NoticeDetail />} />
            <Route path="*" element={<NotFound />} />
          </Route>
        </Routes>
      </AppStateProvider>
    </BrowserRouter>
  </StrictMode>,
);
