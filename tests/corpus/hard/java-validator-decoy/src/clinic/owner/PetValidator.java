package clinic.owner;

import clinic.model.Pet;
import org.springframework.validation.Errors;
import org.springframework.validation.Validator;

public class PetValidator implements Validator {
    @Override
    public void validate(Object obj, Errors errors) {
        Pet pet = (Pet) obj;
        if (pet.getName() == null) {
            errors.rejectValue("name", "required");
        }
    }
}
