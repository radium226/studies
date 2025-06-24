export type RecipeNameProps = {
    initialValue: string | null;
    onValueChange: (value: string | null) => void;
};


export default function RecipeName({ initialValue, onValueChange }: RecipeNameProps) {
    const handleChange = (event: React.ChangeEvent<HTMLInputElement>) => {
        onValueChange(event.target.value ?? null);
    };

    return (
        <div>
            <label htmlFor="recipe-name">Recipe Name: &nbsp;</label>
            <input
                id="recipe-name"
                type="text"
                value={ initialValue ?? '' }
                onChange={ handleChange }
                placeholder="Enter recipe name"
            />
        </div>
    );
}
