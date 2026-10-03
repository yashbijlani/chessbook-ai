import { NavLink, Route, Routes } from "react-router-dom";
import { LibraryPage } from "@/pages/LibraryPage";
import { BookPage } from "@/pages/BookPage";
import { ReaderPage } from "@/pages/ReaderPage";
import { SearchPage } from "@/pages/SearchPage";

export default function App() {
  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <span className="mark">♞</span>
          Chessbook <small>AI</small>
        </div>
        <nav>
          <NavLink to="/library">Library</NavLink>
          <NavLink to="/search">Search</NavLink>
        </nav>
      </header>
      <main className="main">
        <Routes>
          <Route path="/" element={<LibraryPage />} />
          <Route path="/library" element={<LibraryPage />} />
          <Route path="/search" element={<SearchPage />} />
          <Route path="/book/:bookId" element={<BookPage />} />
          <Route path="/book/:bookId/game/:gameId" element={<ReaderPage />} />
          <Route path="*" element={<LibraryPage />} />
        </Routes>
      </main>
    </div>
  );
}
