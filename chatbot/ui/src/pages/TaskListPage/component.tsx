import { useTaskListStore } from "./store";


export type TaskListPageProps = {

}

export default function TaskListPage({ }: TaskListPageProps) {
    const { tasks, toggleTask, addTask, removeTask } = useTaskListStore();
    
    return (
        <>
            <ul>
                { tasks.map((task, index) => (
                    <li 
                        key={ index }
                        onClick={ () => toggleTask(task) }
                    >
                        <span 
                            className={ `${task.completed ? "line-through" : ''} cursor-pointer` }
                        >
                            {task.title}
                        </span>
                        &nbsp;
                        <span
                            className="cursor-pointer"
                            onClick={ () => {
                                removeTask(task);
                            } }
                        >
                            X
                        </span>
                    </li>
                )) }
            </ul>
            <div>
                <input
                    type="text"
                    placeholder="Add a new task"
                    onKeyDown={ (e) => {
                        if (e.key === "Enter" && e.currentTarget.value.trim()) {
                            addTask({ title: e.currentTarget.value, completed: false });
                            e.currentTarget.value = '';
                        }
                    } }
                />
            </div>
        </>
    );
}