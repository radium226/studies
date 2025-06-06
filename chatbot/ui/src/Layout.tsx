import { Outlet, Link, useNavigate, useLocation } from 'react-router';

import { useSettingsStore } from './stores/settings';

import { useTaskListStore } from './pages/TaskListPage';

import Bot from './Bot';


export type LayoutProps = {
    
}

const COLOR_MAPPING: Record<string, string> = {
  red: 'bg-red-500',
  green: 'bg-green-500',
  blue: 'bg-blue-500',
  yellow: 'bg-yellow-500',
  purple: 'bg-purple-500',
  orange: 'bg-orange-500',
}

const TO_MAPPING: Record<string, string> = {
  'welcome': '/',
  'settings': '/settings',
  'tasks': '/tasks',
}

export default function Layout(props: LayoutProps) {
  const navigate = useNavigate();
  const settingsStore = useSettingsStore();

  const { addTask } = useTaskListStore();

  const { pathname } = useLocation();


  console.log("settingsStore", settingsStore);

  return (
    <div>
      <div className="flex flex-col min-h-screen">
        <header className="sticky top-0 bg-gray-800 text-white p-4">
          <ul>
            <li>
              <Link to="/">Welcome</Link>
            </li>
            <li>
            <Link to="/settings">Settings</Link>
            </li>
          </ul>
        </header>
        <main className="flex-grow">
          <Outlet />
        </main>
        <footer className={ `bg-gray-800 text-white p-4 mt-4` }>
          This is the footer.
        </footer>
        <Bot
          backgrounColor={ settingsStore.color }
          onAction={ (action) => {
            console.log('Action received:', action);
            switch (action.type) {
              case 'navigate':
                navigate(TO_MAPPING[action.to]);
                break;

              case 'change-color':
                settingsStore.setColor(COLOR_MAPPING[action.color]);
                break;

              case 'update-email':
                settingsStore.setEmail(action.email);
                break;

              case 'add-task':
                const { taskTitle } = action;
                addTask({ title: taskTitle, completed: false });
                break;
            }
          } }
        />
      </div>
    </div>
  );
}