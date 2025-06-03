import { useEmail } from '../contexts/settings';

export type SettingsProps = {

};

export default function Settings({ }: SettingsProps) {
  const [email, _] = useEmail();
  return (
    <div>
      <h1>Settings</h1>
      <p>This is the settings page where you can configure your application preferences.</p>
      <input 
        type="text" 
        placeholder="Enter your preference"
        value={ email }
        onChange={ (event) => console.log(`Preference changed to: ${event.target.value}`)}
      />
    </div>
  );
}