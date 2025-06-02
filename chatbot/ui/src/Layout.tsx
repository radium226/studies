import { Outlet, Link, useNavigate } from 'react-router';
import { useState, useRef, useEffect } from 'react';

import Bot from './Bot';


export type LayoutProps = {
    
}


export default function Layout(props: LayoutProps) {
  const navigate = useNavigate();
  const [color, setColor] = useState('red');

  return (
    <div>
      <div className="flex flex-col min-h-screen">
        <header className="sticky top-0 bg-gray-800 text-white p-4">
          <ul>
            <li>
              <Link to="/">Welcome</Link>
            </li>
            <li>
            <Link to="/Settings">Settings</Link>
            </li>
          </ul>
        </header>
        <main className="flex-grow">
          <Outlet />
        </main>
        <footer className={ `${color} text-white p-4 mt-4` }>
          This is the footer.
        </footer>
        <Bot
          color={ color }
          onAction={ (action) => {
            console.log('Action received:', action);
            switch (action.type) {
              case 'navigate':
                navigate(action.to);
                break;

              case 'change-color':
                setColor(action.color);
                break;
            }
          } }
        />
      </div>
    </div>
  );
}