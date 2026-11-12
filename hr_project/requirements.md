# Overview
Build an agentic ai system to evaluate candidate resumes
A specific team will give the HR department with wants and needs in their requirements (as-is process)
HR will work with those requirements and upload a job listing
Open it up to the public for people to apply and the HR team will pre-screen the applicants
If the applicant doesn't meet the needed requirements, they won't make it to the second round.
Those that made it through the next round will go to a different department and will be scored from 1-100. Linear scale 100 is twice as good as 50

Prescreen and rankings against the job description that hr and the department had come up with. 

Inputs might be job description (hard requirements, soft requirements), resume/cv, cover letter. Output would be score 1-100 

# Requirements
Latency not super important, just if it does a good job - couple minutes per applicant is fine.
Cannot get sued over this - traceability is important in its recommendations.
Hallucinations - cannot have it making up facts. 
Senior management wants AI for actual rankings
Budget is not an issue
Replicate the same process that a hiring managing team would have. 
Have hiring manager provide weights in the resume of what's actually important
Perhaps have cover letter be applied in later versions
Put emphasis on soft requirements over hard requirements
No additional inputs outside of resume/cv, cover letter, job description
Need to limit hallucinations - reproducibility is very important for auditing.
Must follow hiring process rules (marital status, pregnancy, etc.)
Prevent bias across different groups (gender, age, race, etc.)
Use meta-prompting to rewrite initial prompt with inputs.
Context management (compacting, summarizing)

Create N resumes 
Bin into high, medium, low quality resumes, assign 30 to each and calculate average score