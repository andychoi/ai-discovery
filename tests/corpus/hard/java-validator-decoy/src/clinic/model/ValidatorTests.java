package clinic.model;

import jakarta.validation.Validator;

public class ValidatorTests {
    private Validator validator;

    public void rejectsEmptyName() {
        Pet pet = new Pet();
        validator.validate(pet);
        pet.getName();
    }
}
