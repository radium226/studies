export type RecipeInstructionsProps = {
    initialValue: string[]
    onValueChange: (value: string[]) => void;
};


export default function RecipeInstructions({ initialValue, onValueChange }: RecipeInstructionsProps) {
    return (
        <div>
            <ul>
                {initialValue.map((instruction, index) => (
                    <li key={index}>
                        <input
                            type="text"
                            value={instruction}
                            onChange={(e) => {
                                const newInstructions = [...initialValue];
                                newInstructions[index] = e.target.value;
                                onValueChange(newInstructions);
                            }}
                            placeholder={`Instruction ${index + 1}`}
                        />
                    </li>
                ))}
            </ul>
            <div>
                <button
                    onClick={() => {
                        onValueChange([...initialValue, '']);
                    }
                    }>
                    Add Instruction
                </button>
            </div>
        </div>
    );
}