import { BrowserRouter, Route, Routes } from 'react-router-dom'
import Home from './pages/Home'
import JobPage from './pages/Job'
import LabelPage from './pages/Label'
import Live from './pages/Live'
import ProductPage from './pages/Product'
import ProductsPage from './pages/Products'
import Scan from './pages/Scan'
import SubmitPage from './pages/Submit'

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<Home />} />
        <Route path="/scan" element={<Scan />} />
        <Route path="/live" element={<Live />} />
        <Route path="/job/:id" element={<JobPage />} />
        <Route path="/products" element={<ProductsPage />} />
        <Route path="/p/:key" element={<ProductPage />} />
        <Route path="/label" element={<LabelPage />} />
        <Route path="/submit" element={<SubmitPage />} />
        <Route path="*" element={<Home />} />
      </Routes>
    </BrowserRouter>
  )
}
