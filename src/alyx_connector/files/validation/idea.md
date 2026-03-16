Plan : 

- define rule with json yml, etc (parseable as python builtings, dict, lists, str, int , etc)
- parse with Validator
- make a list of Encapsulated objects
- evaluate should find wich rule to use
- execute (or apply) should run through the triggers, and schedule next triggers, (add them to the "planned triggers" list. Executed ones are added to "executed triggers"). If arguments given to execute() (a list of triggers allowed to run) contain planned triggers not executed (in added order) then they are ran, and if they do add triggers, they are run in in the list of curentely allowed trigers, else just planned, etc. Until no planned trigger that is allowed to run, is not executed.
Making sure planned triggers are allowed to be executed is up to the user (not a feature of the validator package).

Need to make sure sets can be "ordered" (to keep order of planned triggers as added, FIFO).