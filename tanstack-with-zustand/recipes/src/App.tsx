import './App.css'
import { doCreateRouter } from './router'
import { RouterProvider } from '@tanstack/react-router'
import { Bot } from './spi'


function App() {
  const bot = new Bot();
  
  return (
    <RouterProvider router={ doCreateRouter(bot) } />
  )
}

export default App
