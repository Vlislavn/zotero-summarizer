import { Navigate, Route, Routes } from 'react-router-dom';
import NavBar from './components/NavBar.jsx';
import AnnotationVerdict from './pages/AnnotationVerdict.jsx';
import Today from './pages/Today.jsx';
import Settings from './pages/Settings.jsx';
import Library from './pages/Library.jsx';
import Triage from './pages/Triage.jsx';
import Review from './pages/Review.jsx';
import Pending from './pages/Pending.jsx';
import Audit from './pages/Audit.jsx';

export default function App() {
  return (
    <div className="min-h-screen px-4 py-5 max-w-[1400px] mx-auto">
      <NavBar />
      <main>
        <Routes>
          <Route path="/" element={<Navigate to="/today" replace />} />
          <Route path="/today" element={<Today />} />
          <Route path="/annotate" element={<AnnotationVerdict />} />
          <Route path="/settings" element={<Settings />} />
          <Route path="/library" element={<Library />} />
          <Route path="/triage" element={<Triage />} />
          <Route path="/review" element={<Review />} />
          <Route path="/pending" element={<Pending />} />
          <Route path="/audit" element={<Audit />} />
          <Route path="*" element={<Navigate to="/today" replace />} />
        </Routes>
      </main>
    </div>
  );
}
