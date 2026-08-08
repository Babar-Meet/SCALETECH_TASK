import { BrowserRouter, Routes, Route, NavLink } from 'react-router-dom';
import { Music, Upload, Link as LinkIcon, DownloadCloud } from 'lucide-react';
import { useEffect, useState } from 'react';
import ImportPage from './pages/ImportPage';
import LinksPage from './pages/LinksPage';
import DownloadsPage from './pages/DownloadsPage';

function App() {
  const [scrolled, setScrolled] = useState(false);

  useEffect(() => {
    const handleScroll = () => {
      setScrolled(window.scrollY > 20);
    };
    window.addEventListener('scroll', handleScroll);
    return () => window.removeEventListener('scroll', handleScroll);
  }, []);

  return (
    <BrowserRouter>
      <nav className={`navbar ${scrolled ? 'scrolled' : ''}`}>
        <div className="nav-brand">
          <Music size={28} />
          <span>N-Music</span>
        </div>
        <div className="nav-links">
          <NavLink to="/" className={({isActive}) => `nav-link ${isActive ? 'active' : ''}`}>
            Home
          </NavLink>
          <NavLink to="/links" className={({isActive}) => `nav-link ${isActive ? 'active' : ''}`}>
            My List
          </NavLink>
          <NavLink to="/downloads" className={({isActive}) => `nav-link ${isActive ? 'active' : ''}`}>
            Downloads
          </NavLink>
        </div>
      </nav>
      
      <main className="container">
        <Routes>
          <Route path="/" element={<ImportPage />} />
          <Route path="/links" element={<LinksPage />} />
          <Route path="/downloads" element={<DownloadsPage />} />
        </Routes>
      </main>
    </BrowserRouter>
  );
}

export default App;
